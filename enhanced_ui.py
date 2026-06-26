import streamlit as st
import json
import base64
import os
import pandas as pd
from utils import InvoiceData, GroqClient, preprocess_image, process_image_upload, process_image_url, display_image_preview, setup_page, show_extraction_button, display_results, display_error, run_chatbot, edit_invoice_data, export_to_csv
from uuid import uuid4
import argparse
from telemetry_validator import telemetry_context_validation
from bl_scanner_app import BL_EXTRACTION_PROMPT, _extract_bl, _map_cargo_type
from email_ingestor import ingest_eml, make_demo_eml, imap_fetch_invoices, make_demo_imap_results

# Invoice type detection
def detect_invoice_type(invoice_data: dict) -> str:
    """Detect invoice type based on keywords in vendor name or line items."""
    keywords = {
        "retail": ["store", "shop", "mart", "sku", "product"],
        "service": ["consulting", "service", "hours", "labor", "professional"],
        "utility": ["electricity", "water", "gas", "bill", "utility"]
    }
    vendor = invoice_data.get("vendor_name", "").lower()
    line_items = invoice_data.get("line_items", [])
    descriptions = [item.get("description", "").lower() for item in line_items]

    for inv_type, kws in keywords.items():
        if any(kw in vendor for kw in kws) or any(any(kw in desc for kw in kws) for desc in descriptions):
            return inv_type
    return "general"

# Fraud detection
def detect_fraud(invoices):
    """Detect potential fraud in invoices using rules."""
    if not invoices:
        st.warning("No invoices to analyze for fraud.")
        return
    
    fraud_data = []
    invoice_numbers = [inv["invoice"].invoice_number for inv in invoices if inv["invoice"].invoice_number]
    duplicates = {num for num in invoice_numbers if invoice_numbers.count(num) > 1}
    
    for inv in invoices:
        invoice = inv["invoice"]
        flags = []
        if invoice.invoice_number in duplicates:
            flags.append("Duplicate invoice number detected.")
        if invoice.total_amount and invoice.total_amount > 100000:
            flags.append("Unusually high total amount.")
        if invoice.tax and invoice.total_amount and invoice.tax > 0.3 * invoice.total_amount:
            flags.append("Unusually high tax amount.")
        if flags:
            fraud_data.append({
                "Invoice ID": inv["image_id"],
                "Invoice Number": invoice.invoice_number,
                "Total Amount": invoice.total_amount,
                "Tax": invoice.tax,
                "Flags": "; ".join(flags)
            })
    
    if fraud_data:
        st.subheader("Potential Fraud Alerts")
        st.dataframe(pd.DataFrame(fraud_data))
    else:
        st.success("No potential fraud detected.")

# Batch processing status
def display_batch_status(invoices):
    """Display summary of processed invoices."""
    total = len(invoices)
    successful = sum(1 for inv in invoices if inv["invoice"].invoice_number is not None)
    st.sidebar.subheader("Batch Processing Status")
    st.sidebar.write(f"Total Invoices: {total}")
    st.sidebar.write(f"Successfully Processed: {successful}")
    st.sidebar.write(f"Success Rate: {successful / total * 100:.1f}%" if total > 0 else "Success Rate: 0%")

def select_input_method():
    """Custom input method selection."""
    return st.radio(
        "Select input method: 📸",
        ["Upload Image 📤", "Image URL 🌐"],
        key="enhanced_input_method"
    )


def _show_eml_result(result: dict, uid_prefix: str = ""):
    """Render VEC analysis and attachments for one parsed email result dict."""
    if result.get("error"):
        st.error(f"Failed to parse email: {result['error']}")
        return

    st.subheader("Email Headers")
    meta_cols = st.columns(2)
    with meta_cols[0]:
        st.markdown(f"**From:** {result['sender'] or '—'}")
        st.markdown(f"**Subject:** {result['subject'] or '—'}")
    with meta_cols[1]:
        st.markdown(f"**Date:** {result['date'] or '—'}")
        reply_to_val = result["reply_to"]
        if reply_to_val:
            st.markdown(f"**Reply-To:** ⚠️ `{reply_to_val}`")
        else:
            st.markdown("**Reply-To:** *(not set)*")

    st.divider()

    vec_risk = result["vec_risk"]
    flags = result["vec_flags"]

    if vec_risk == "HIGH":
        st.error("🚨 HIGH VEC RISK — Strong indicators of Vendor Email Compromise detected")
    elif vec_risk == "MEDIUM":
        st.warning("⚠️ MEDIUM VEC RISK — Suspicious header patterns found")
    elif vec_risk == "LOW":
        st.warning("⚠️ LOW VEC RISK — Minor indicators; proceed with caution")
    else:
        st.success("✅ No VEC indicators detected in email headers")

    if flags:
        st.subheader("VEC Flag Details")
        for flag in flags:
            sev = flag["severity"]
            icon = "🚨" if sev == "HIGH" else ("⚠️" if sev == "MEDIUM" else "🔶")
            st.markdown(f"{icon} **[{sev}]** `{flag['code']}` — {flag['detail']}")

    st.divider()

    attachments = result["attachments"]
    st.subheader(f"Attachments ({len(attachments)} found)")

    if not attachments:
        st.info("No invoice attachments (PDF or image) found in this email.")
    else:
        for idx, att in enumerate(attachments):
            att_label = f"📎 {att['filename']} ({att['content_type']})"
            with st.expander(att_label, expanded=(idx == 0)):
                if att["is_image"]:
                    st.image(att["bytes"], caption=att["filename"], use_container_width=True)
                    btn_key = f"extract_att_{uid_prefix}_{idx}"
                    if st.button(f"Extract Invoice Data from {att['filename']}", key=btn_key):
                        groq_client = GroqClient(st.session_state.groq_api_key)
                        with st.spinner("Extracting invoice data via LLaMA…"):
                            invoice_data = groq_client.extract_invoice_data(
                                att["bytes"], att["content_type"]
                            )
                        if invoice_data:
                            st.session_state.invoices.append(invoice_data)
                            st.session_state.invoice_data = invoice_data
                            st.success(
                                f"Invoice extracted: {invoice_data.invoice_number}. "
                                "Pre-filled into Telemetry Validation tab."
                            )
                            st.session_state["tel_invoice_date"] = invoice_data.invoice_date or ""
                            st.rerun()
                        else:
                            st.error("Extraction failed — check that the image is a legible invoice.")
                else:
                    st.markdown(
                        f"**{att['filename']}** ({att['content_type']}, "
                        f"{len(att['bytes']):,} bytes)"
                    )
                    st.info(
                        "PDF extraction requires a PDF-to-image conversion step. "
                        "Save as PNG/JPEG and upload via the Invoice Extraction tab."
                    )

    if vec_risk in ("HIGH", "MEDIUM"):
        st.divider()
        st.subheader("Recommended Actions")
        st.markdown(
            "- **Do not process payment** until the invoice is verified via a known-good contact.\n"
            "- Call the vendor using a phone number from your existing records — not from this email.\n"
            "- Forward the email to your security team for header forensics.\n"
            "- If an IBAN was changed, cross-check it against the **Telemetry Validation** tab."
        )


def enhanced_ui():
    # Setup page
    setup_page()

    # Enhanced CSS
    st.markdown("""
        <style>
        .stApp {
            background-image: url('https://img.freepik.com/premium-photo/directly-shot-blank-book-by-laptop-blue-background_1048944-12723282.jpg');
            background-size: cover;
            background-attachment: fixed;
            background-position: center;
            min-height: 100vh;
            padding: 20px;
        }
        [data-testid="stAppViewContainer"] {
            background-image: url();
        }
        .st-expander, .stAlert, .stTextInput, .stSelectbox, .stFileUploader {
            background-color: rgba(255, 255, 255, 0.95);
            border-radius: 12px;
            padding: 15px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            margin-bottom: 15px;
        }
        .stButton>button {
            background-color: #4b7bec;
            color: white;
            border-radius: 8px;
            padding: 10px 20px;
            font-weight: 500;
            transition: all 0.3s ease;
        }
        .stButton>button:hover {
            background-color: #3867d6;
            transform: translateY(-2px);
        }
        [data-testid="stSidebar"] {
            background-color: #2c3e50;
            color: white;
            border-radius: 8px;
            padding: 15px;
        }
        .stTabs [data-baseweb="tab"] {
            background-color: rgba(255, 255, 255, 0.9);
            border-radius: 8px;
            margin: 5px;
            padding: 10px;
        }
        .stTabs [data-baseweb="tab"]:hover {
            background-color: #4b7bec;
            color: white;
        }
        [data-theme="dark"] .stApp,
        [data-theme="dark"] [data-testid="stAppViewContainer"] {
            background-image: url('https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSna4C-cCRS0Zf7r3rgPd1tn5PQhqAyJrBtlOvnuwroOYOzutkPzfg2jF9tn8YbArUiMCk&usqp=CAU');
        }
        [data-theme="dark"] h1, [data-theme="dark"] h2, [data-theme="dark"] h3 {
            color: #e0e0e0;
        }
        [data-theme="dark"] .st-expander, [data-theme="dark"] .stAlert, 
        [data-theme="dark"] .stTextInput, [data-theme="dark"] .stSelectbox, 
        [data-theme="dark"] .stFileUploader {
            background-color: rgba(45, 55, 72, 0.95);
            color: #e0e0e0;
        }
        .low-confidence {
            background-color: rgba(255, 99, 132, 0.2);
        }
        </style>
    """, unsafe_allow_html=True)

    # Sidebar settings
    st.sidebar.title("Invoice OCR Dashboard")
    st.sidebar.markdown("### Settings")
    language = st.sidebar.selectbox(
        "Select Invoice Language",
        ["Tamil","English", "Spanish", "French", "German", "Other"],
        key="language_select"
    )
    theme = st.sidebar.selectbox(
        "Theme",
        ["Light", "Dark"],
        key="theme_select"
    )

    # Initialize session state
    if "invoices" not in st.session_state:
        st.session_state.invoices = []
    if "groq_api_key" not in st.session_state:
        st.session_state.groq_api_key = None
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # API key setup
    parser = argparse.ArgumentParser(description='Run the Streamlit app.')
    parser.add_argument('--environment', type=str, choices=['local', 'cloud'], default='cloud')
    args = parser.parse_args()
    
    if args.environment == 'cloud':
        try:
            groq_api_key = st.secrets["GROQ_API_KEY"]
        except KeyError:
            st.error("GROQ_API_KEY not found in Streamlit secrets.")
            return
    else:
        from dotenv import load_dotenv
        import os
        load_dotenv()
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            st.error("GROQ_API_KEY not found in environment variables.")
            return
    
    st.session_state.groq_api_key = groq_api_key

    # Tabs
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📄 Invoice Extraction", "🤖 Chatbot", "🚨 Fraud Detection", "🛰️ Telemetry Validation", "📧 Email Ingestion", "🚢 B/L Scanner"])

    with tab1:
        st.header("Invoice Extraction")
        input_method = select_input_method()
        image_bytes_list = []
        mime_types = []
        
        with st.container():
            st.subheader("Upload Invoices")
            with st.expander("Input Options", expanded=True):
                if input_method == "Upload Image 📤":
                    uploaded_files = st.file_uploader(
                        "Upload invoice images (supports multiple)",
                        type=["png", "jpg", "jpeg"],
                        accept_multiple_files=True,
                        key="batch_uploader"
                    )
                    if uploaded_files:
                        for uploaded_file in uploaded_files:
                            image_bytes, mime_type = process_image_upload(uploaded_file)
                            if image_bytes:
                                try:
                                    image_bytes = preprocess_image(image_bytes)
                                    image_bytes_list.append(image_bytes)
                                    mime_types.append(mime_type)
                                    st.success(f"Image {uploaded_file.name} uploaded successfully!")
                                except Exception as e:
                                    display_error(f"Image preprocessing failed: {str(e)}. Ensure the image is clear.")
                else:
                    image_url = st.text_input(
                        "Enter image URL:",
                        key="url_input",
                        placeholder="https://example.com/invoice.jpg"
                    )
                    if image_url:
                        try:
                            image_bytes = process_image_url(image_url)
                            if image_bytes:
                                image_bytes = preprocess_image(image_bytes)
                                image_bytes_list.append(image_bytes)
                                mime_types.append("image/jpeg")
                                st.success("Image URL processed successfully!")
                        except ValueError as e:
                            display_error(str(e))
        
        if image_bytes_list:
            col1, col2 = st.columns([1, 2], gap="medium")
            with col1:
                st.subheader("Invoice Images")
                for i, image_bytes in enumerate(image_bytes_list):
                    st.write(f"Image {i+1}")
                    display_image_preview(image_bytes)
            
            with col2:
                st.subheader("Extracted Invoice Data")
                if show_extraction_button():
                    progress_bar = st.progress(0)
                    for i, (image_bytes, mime_type) in enumerate(zip(image_bytes_list, mime_types)):
                        with st.spinner(f"Extracting data from image {i+1}..."):
                            try:
                                groq_client = GroqClient(api_key=st.session_state.groq_api_key)
                                # Dynamic OCR prompt
                                initial_prompt = """
                                You are an intelligent OCR extraction agent capable of understanding and processing invoices in {language}.
                                Extract all relevant information from the provided invoice image in structured JSON format.
                                The JSON object must follow this schema: {schema}.
                                Include a confidence score (0.0 to 1.0) for each extracted field in a separate 'confidence_scores' object.
                                If a field cannot be found, return it as null.
                                Look for common invoice patterns such as:
                                - Invoice number: Often labeled as 'Invoice #', 'No.', or similar.
                                - Dates: Look for 'Date', 'Issued', 'Due', in formats like MM/DD/YYYY or DD/MM/YYYY.
                                - Addresses: Look for 'Bill to', 'Ship to', or multi-line address blocks.
                                - Line items: Tables or lists with description, quantity, unit price, and total.
                                - Totals: Look for 'Subtotal', 'Tax', 'Total', often at the bottom.
                                - Currency: Look for symbols ($, €, £) or codes (USD, EUR).
                                Return the result strictly in JSON format with 'data' and 'confidence_scores' keys.
                                """
                                base64_image = base64.b64encode(image_bytes).decode("utf-8")
                                image_content = {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
                                }
                                initial_data = groq_client.extract_invoice_data(
                                    initial_prompt.format(
                                        language=language,
                                        schema=json.dumps(InvoiceData.model_json_schema(), indent=2)
                                    ),
                                    image_content
                                )
                                invoice_type = detect_invoice_type(initial_data.get("data", {}))
                                
                                type_specific_prompts = {
                                    "retail": "Focus on product SKUs, quantities, and unit prices in line items.",
                                    "service": "Emphasize service descriptions, hours worked, and rates in line items.",
                                    "utility": "Prioritize billing periods, meter readings, and rate structures.",
                                    "general": "Extract all fields as per the schema."
                                }
                                prompt = initial_prompt + f"\nSpecific instructions for {invoice_type} invoices: {type_specific_prompts[invoice_type]}"
                                
                                max_retries = 2
                                for attempt in range(max_retries):
                                    try:
                                        extracted_data = groq_client.extract_invoice_data(
                                            prompt.format(
                                                language=language,
                                                schema=json.dumps(InvoiceData.model_json_schema(), indent=2)
                                            ),
                                            image_content
                                        )
                                        invoice = InvoiceData(**extracted_data.get("data", {}))
                                        confidence_scores = extracted_data.get("confidence_scores", {})
                                        
                                        if all(value is None for value in extracted_data.get("data", {}).values()):
                                            st.warning(f"Image {i+1}, Attempt {attempt + 1}: No data extracted. Retrying..." if attempt < max_retries - 1 else f"Image {i+1}: All attempts failed.")
                                            continue
                                        
                                        st.session_state.invoices.append({
                                            "invoice": invoice,
                                            "confidence_scores": confidence_scores,
                                            "image_id": str(uuid4()),
                                            "invoice_type": invoice_type
                                        })
                                        display_results(invoice)
                                        st.subheader("Confidence Scores")
                                        st.json(confidence_scores)
                                        st.info(f"Detected Invoice Type: {invoice_type.capitalize()}")
                                        st.success(f"Image {i+1} processed successfully!")
                                        break
                                    
                                    except Exception as e:
                                        if attempt < max_retries - 1:
                                            st.warning(f"Image {i+1}, Attempt {attempt + 1} failed: {str(e)}. Retrying...")
                                            continue
                                        display_error(f"Image {i+1}: Failed to parse after {max_retries} attempts: {str(e)}")
                            
                            except Exception as e:
                                display_error(f"Image {i+1}: Failed to parse: {str(e)}. Try a clearer image.")
                        
                        progress_bar.progress((i + 1) / len(image_bytes_list))
                
                # Data editing with validation feedback
                if st.session_state.invoices:
                    st.subheader("Edit Invoices")
                    invoice_data = [
                        {
                            "Invoice ID": inv["image_id"],
                            "Invoice Number": inv["invoice"].invoice_number,
                            "Total Amount": inv["invoice"].total_amount,
                            "Tax": inv["invoice"].tax,
                            "Date": inv["invoice"].invoice_date,
                            "Invoice Type": inv["invoice_type"],
                            "Confidence (Invoice Number)": inv["confidence_scores"].get("invoice_number", 1.0),
                            "Confidence (Total Amount)": inv["confidence_scores"].get("total_amount", 1.0),
                            "Confidence (Tax)": inv["confidence_scores"].get("tax", 1.0)
                        } for inv in st.session_state.invoices
                    ]
                    def highlight_low_confidence(row):
                        styles = [""] * len(row)
                        for i, col in enumerate(row.index):
                            if "Confidence" in col and row[col] < 0.7:
                                styles[i] = "background-color: rgba(255, 99, 132, 0.2)"
                        return styles
                    
                    edited_df = st.data_editor(
                        pd.DataFrame(invoice_data),
                        column_config={
                            "Invoice ID": {"editable": False},
                            "Invoice Number": {"type": "text"},
                            "Total Amount": {"type": "number"},
                            "Tax": {"type": "number"},
                            "Date": {"type": "text"},
                            "Invoice Type": {"editable": False},
                            "Confidence (Invoice Number)": {"editable": False},
                            "Confidence (Total Amount)": {"editable": False},
                            "Confidence (Tax)": {"editable": False}
                        },
                        key="invoice_editor"
                    )
                    st.dataframe(edited_df.style.apply(highlight_low_confidence, axis=1))
                    if st.button("Save Edited Data", key="save_edit"):
                        for i, row in edited_df.iterrows():
                            for inv in st.session_state.invoices:
                                if inv["image_id"] == row["Invoice ID"]:
                                    inv["invoice"].invoice_number = row["Invoice Number"]
                                    inv["invoice"].total_amount = row["Total Amount"]
                                    inv["invoice"].tax = row["Tax"]
                                    inv["invoice"].invoice_date = row["Date"]
                        st.success("✅ Data updated successfully!")
                
                # Export options
                st.subheader("Export Data")
                col_export1, col_export2 = st.columns(2)
                with col_export1:
                    if st.button("Download All as CSV", key="csv_button"):
                        if st.session_state.invoices:
                            csv_data = export_to_csv([inv["invoice"] for inv in st.session_state.invoices])
                            st.download_button(
                                label="Download CSV",
                                data=csv_data,
                                file_name="all_invoices.csv",
                                mime="text/csv",
                                key="csv_download"
                            )
                with col_export2:
                    if st.button("Download All as JSON", key="json_button"):
                        if st.session_state.invoices:
                            json_data = json.dumps([inv["invoice"].dict() for inv in st.session_state.invoices], indent=2)
                            st.download_button(
                                label="Download JSON",
                                data=json_data,
                                file_name="all_invoices.json",
                                mime="application/json",
                                key="json_download"
                            )

    with tab2:
        st.header("Invoice Assistant Chatbot")
        with st.container():
            predefined_prompts = [
                "Summarize the latest invoice",
                "Check for missing fields in invoices",
                "List all vendors",
                "What is the total amount of all invoices?"
            ]
            selected_prompt = st.selectbox("Quick Questions", [""] + predefined_prompts, key="predefined_prompt")
            
            invoice_context = json.dumps([inv["invoice"].dict() for inv in st.session_state.invoices], indent=2)
            user_input = st.text_input("Ask a question about your invoices:", key="chat_input")
            
            if user_input or selected_prompt:
                prompt = selected_prompt or user_input
                full_prompt = f"""
                You are an invoice processing assistant. Use the following invoice data as context:
                {invoice_context}
                Answer the user's question: {prompt}
                Provide a concise, accurate response. If the question is unrelated to invoices, politely redirect to invoice-related queries.
                """
                try:
                    groq_client = GroqClient(api_key=st.session_state.groq_api_key)
                    response = groq_client.run_chatbot_query(full_prompt)
                    st.session_state.chat_history.append({"user": prompt, "bot": response})
                    st.markdown("**Response:**")
                    st.markdown(response)
                except Exception as e:
                    st.error(f"Chatbot error: {str(e)}. Please try again.")
            
            st.sidebar.subheader("Chat History")
            for i, chat in enumerate(st.session_state.chat_history):
                with st.sidebar.expander(f"Chat {i+1}"):
                    st.write(f"**You:** {chat['user']}")
                    st.write(f"**Bot:** {chat['bot']}")

    with tab3:
        st.header("Fraud Detection")
        if st.session_state.invoices:
            detect_fraud(st.session_state.invoices)
        else:
            st.info("No invoices processed yet. Upload invoices in the Extraction tab.")

    with tab4:
        st.header("Telemetry Validation")
        st.markdown(
            "> **Physical reality check:** Cross-references invoice logistics identifiers "
            "against the AIS maritime registry. If the ship didn't dock, the payment doesn't clear."
        )

        telemetry_enabled = st.toggle("Enable Telemetry Validation", value=False, key="telemetry_toggle")

        if not telemetry_enabled:
            st.info("Toggle on Telemetry Validation above to begin physical event verification.")
        else:
            # Resolve API keys — MarineTraffic takes priority, then AISStream, then mock
            mt_api_key = None
            try:
                mt_api_key = st.secrets.get("MARINETRAFFIC_API_KEY")
            except Exception:
                pass
            if not mt_api_key:
                mt_api_key = os.getenv("MARINETRAFFIC_API_KEY")

            ais_api_key = None
            if not mt_api_key:
                try:
                    ais_api_key = st.secrets.get("AISSTREAM_API_KEY")
                except Exception:
                    pass
                if not ais_api_key:
                    ais_api_key = os.getenv("AISSTREAM_API_KEY")

            if mt_api_key:
                st.info("Live mode — port of discharge verified via MarineTraffic historical port calls.")
            elif ais_api_key:
                st.info(
                    "Live mode — port of discharge verified via AISStream real-time feed. "
                    "Best for vessels currently in port; pair with MarineTraffic for historical records."
                )
            else:
                st.warning("Mock mode — no API key found (MARINETRAFFIC_API_KEY or AISSTREAM_API_KEY). Using local registry.")

            ais_window_seconds = 30
            if ais_api_key and not mt_api_key:
                ais_window_seconds = st.slider(
                    "AIS listen window (seconds)",
                    min_value=10, max_value=120, value=30, step=10,
                    help="How long to listen on the live AIS feed before marking the vessel as absent.",
                )

            DEMO_SCENARIOS = {
                "✅ CLEAR — Vessel docked, port matches, IBAN correct": {
                    "mmsi": "244170218",
                    "discharge_port": "Port of Rotterdam",
                    "invoice_date": "2026-06-21",
                    "submission_date": "2026-06-23",
                    "iban": "NL91ABNA0417164300",
                },
                "🚨 BLOCKED — Port mismatch (fraudulent discharge port)": {
                    "mmsi": "244170218",
                    "discharge_port": "Port of Antwerp",
                    "invoice_date": "2026-06-21",
                    "submission_date": "",
                    "iban": "NL91ABNA0417164300",
                },
                "🚨 BLOCKED — Unknown vessel (not in registry)": {
                    "mmsi": "000000000",
                    "discharge_port": "Port of Rotterdam",
                    "invoice_date": "2026-06-21",
                    "submission_date": "",
                    "iban": "NL91ABNA0417164300",
                },
                "🚨 BLOCKED — IBAN hijacked (account substitution)": {
                    "mmsi": "211456200",
                    "discharge_port": "Port of Hamburg",
                    "invoice_date": "2026-06-19",
                    "submission_date": "",
                    "iban": "GB29NWBK60161331926819",
                },
                "🚨 BLOCKED — Date anomaly (Case A — forged BL date)": {
                    "mmsi": "244170218",
                    "discharge_port": "Port of Rotterdam",
                    "invoice_date": "2026-01-01",
                    "submission_date": "",
                    "iban": "NL91ABNA0417164300",
                },
                "🚨 BLOCKED — Late submission (Case D — fake agency invoice)": {
                    "mmsi": "244170218",
                    "discharge_port": "Port of Rotterdam",
                    "invoice_date": "2026-06-21",
                    "submission_date": "2026-07-25",
                    "iban": "NL91ABNA0417164300",
                    "cargo_type": "",
                    "voyage_id": "",
                    "cargo_quantity_mt": "",
                },
                "🚨 BLOCKED — Duplicate voyage / ghost cargo (Case B)": {
                    "mmsi": "244170218",
                    "discharge_port": "Port of Rotterdam",
                    "invoice_date": "2026-06-21",
                    "submission_date": "",
                    "iban": "NL91ABNA0417164300",
                    "cargo_type": "containers",
                    "voyage_id": "VOY-2026-441",
                    "cargo_quantity_mt": "",
                },
                "🚨 BLOCKED — Cargo type mismatch (Case B — tanker vs grain)": {
                    "mmsi": "224143870",
                    "discharge_port": "Port of Barcelona",
                    "invoice_date": "2026-06-23",
                    "submission_date": "",
                    "iban": "ES9121000418450200051332",
                    "cargo_type": "grain",
                    "voyage_id": "",
                    "cargo_quantity_mt": "",
                },
                "🚨 BLOCKED — DWT exceeded (Case C — quantity overstated)": {
                    "mmsi": "211456200",
                    "discharge_port": "Port of Hamburg",
                    "invoice_date": "2026-06-19",
                    "submission_date": "",
                    "iban": "DE89370400440532013000",
                    "cargo_type": "grain",
                    "voyage_id": "",
                    "cargo_quantity_mt": "60000",
                },
                "✏️ Manual input": None,
            }

            mode = st.radio(
                "Invoice scenario",
                list(DEMO_SCENARIOS.keys()),
                key="telemetry_mode",
                help="Select a demo scenario or enter invoice details manually.",
            )

            prefill = DEMO_SCENARIOS[mode] or {}

            # Pre-populate invoice_date from last extracted invoice when in manual mode
            extracted_invoice_date = ""
            if st.session_state.get("invoices"):
                last_date = st.session_state.invoices[-1]["invoice"].invoice_date
                if last_date:
                    extracted_invoice_date = last_date

            with st.form("telemetry_form"):
                st.subheader("Invoice Logistics Identifiers")
                col_a, col_b = st.columns(2)
                with col_a:
                    mmsi = st.text_input("MMSI", value=prefill.get("mmsi", ""), placeholder="e.g. 244170218")
                    invoice_date_default = prefill.get("invoice_date") or extracted_invoice_date
                    invoice_date = st.text_input("Invoice Date", value=invoice_date_default, placeholder="YYYY-MM-DD")
                    submission_date = st.text_input("Submission Date (optional)", value=prefill.get("submission_date", ""), placeholder="YYYY-MM-DD")
                with col_b:
                    discharge_port = st.text_input("Port of Discharge", value=prefill.get("discharge_port", ""), placeholder="e.g. Port of Rotterdam")
                    iban = st.text_input("Beneficiary IBAN", value=prefill.get("iban", ""), placeholder="e.g. NL91ABNA0417164300")

                with st.expander("Cargo Checks — Cases B & C (optional)"):
                    col_c, col_d = st.columns(2)
                    with col_c:
                        voyage_id = st.text_input("Voyage ID", value=prefill.get("voyage_id", ""), placeholder="e.g. VOY-2026-441")
                        cargo_type = st.text_input("Cargo Type", value=prefill.get("cargo_type", ""), placeholder="e.g. grain, crude_oil, containers")
                    with col_d:
                        cargo_qty_str = prefill.get("cargo_quantity_mt", "") or ""
                        cargo_qty_val = float(cargo_qty_str) if cargo_qty_str else 0.0
                        cargo_quantity_mt = st.number_input("Cargo Quantity (MT)", min_value=0.0, value=cargo_qty_val, step=100.0)

                submitted = st.form_submit_button("Run Telemetry Check", type="primary")

            if extracted_invoice_date and mode == "✏️ Manual input":
                st.caption(f"Invoice date pre-filled from last extracted invoice ({extracted_invoice_date}).")

            if submitted:
                invoice_payload = {
                    "mmsi": mmsi,
                    "discharge_port": discharge_port,
                    "invoice_date": invoice_date,
                    "submission_date": submission_date,
                    "iban": iban,
                    "cargo_type": cargo_type,
                    "voyage_id": voyage_id,
                    "cargo_quantity_mt": cargo_quantity_mt if cargo_quantity_mt > 0 else None,
                }
                result = telemetry_context_validation(
                    invoice_payload,
                    marinetraffic_api_key=mt_api_key,
                    aisstream_api_key=ais_api_key,
                    ais_window_seconds=ais_window_seconds,
                )

                st.divider()
                st.subheader("Validation Result")

                verdict = result.get("verdict", "BLOCKED" if result["is_tampered"] else "CLEAR")
                if verdict == "BLOCKED":
                    st.error("HIGH RISK — Telemetry mismatch detected")
                    st.markdown(f"**Risk score:** `{result['risk_score']}`")
                    st.markdown(f"**Reason:** {result['overall_reason']}")
                elif verdict == "REVIEW":
                    st.warning("NEEDS REVIEW — Could not fully verify against AIS data")
                    st.markdown(f"**Risk score:** `{result['risk_score']}`")
                    st.markdown(f"**Details:** {result['overall_reason']}")
                else:
                    st.success("Telemetry CLEAR — Physical event confirmed")
                    st.markdown(f"**Risk score:** `{result['risk_score']}`")
                    st.markdown(f"**Status:** {result['overall_reason']}")

                if result.get("vessel_name"):
                    st.markdown(
                        f"**Vessel:** {result['vessel_name']} &nbsp;|&nbsp; "
                        f"**Carrier:** {result.get('carrier', '—')} &nbsp;|&nbsp; "
                        f"**Last dock date:** {result.get('dock_date', '—')} &nbsp;|&nbsp; "
                        f"**Source:** `{result.get('source', '—')}`",
                        unsafe_allow_html=True,
                    )

                st.subheader("Check Breakdown")
                for check in result.get("checks", []):
                    if check["status"] == "PASS":
                        icon = "✅"
                    elif check["status"] == "WARN":
                        icon = "⚠️"
                    else:
                        icon = "❌"
                    st.markdown(f"{icon} **`{check['field']}`** — {check['detail']}")

    # --- Tab 5: Email Ingestion / VEC Detection ---
    with tab5:
        st.header("Email Ingestion — VEC Detection")
        st.markdown(
            "> Upload a forwarded invoice email (`.eml`) to detect **Vendor Email Compromise** "
            "header indicators and extract any attached invoice documents for validation."
        )

        input_mode = st.radio(
            "Input method",
            ["Upload .eml file", "Live Mailbox (IMAP)", "Demo scenario"],
            horizontal=True,
            key="email_input_mode",
        )

        eml_bytes = None

        if input_mode == "Upload .eml file":
            uploaded_eml = st.file_uploader("Upload invoice email (.eml)", type=["eml"], key="eml_uploader")
            if uploaded_eml:
                eml_bytes = uploaded_eml.read()

        elif input_mode == "Live Mailbox (IMAP)":
            # ── Demo inbox ───────────────────────────────────────────────────
            with st.expander("Try demo inbox (no credentials needed)", expanded=False):
                st.caption(
                    "Simulates fetching 3 unread invoice emails from an accounts payable inbox: "
                    "one clean, one suspicious sender, one VEC attack."
                )
                col_demo, col_clear = st.columns([1, 1])
                with col_demo:
                    if st.button("Load demo inbox", key="imap_demo_load"):
                        st.session_state["imap_demo_results"] = make_demo_imap_results()
                with col_clear:
                    if st.button("Clear", key="imap_demo_clear"):
                        st.session_state.pop("imap_demo_results", None)

            if st.session_state.get("imap_demo_results"):
                demo_msgs = st.session_state["imap_demo_results"]
                risk_labels = {
                    "HIGH": "🚨 HIGH RISK",
                    "MEDIUM": "⚠️ MEDIUM RISK",
                    "LOW": "🔶 LOW RISK",
                    "CLEAN": "✅ CLEAN",
                }
                st.success(f"Demo inbox: {len(demo_msgs)} message(s) fetched.")
                for i, msg_result in enumerate(demo_msgs):
                    risk = msg_result.get("vec_risk", "CLEAN")
                    label = f"{risk_labels.get(risk, risk)} — {msg_result.get('subject') or f'Message {i + 1}'}"
                    with st.expander(f"📧 {label}", expanded=(i == 0)):
                        _show_eml_result(msg_result, uid_prefix=msg_result.get("uid", str(i)))
                st.divider()

            # ── Live connection ───────────────────────────────────────────────
            IMAP_PRESETS = {
                "Gmail":             ("imap.gmail.com",        993),
                "Outlook / Hotmail": ("outlook.office365.com", 993),
                "Yahoo":             ("imap.mail.yahoo.com",   993),
                "Custom":            ("",                      993),
            }
            preset = st.selectbox("Provider preset", list(IMAP_PRESETS.keys()), key="imap_preset")
            default_host, default_port = IMAP_PRESETS[preset]

            with st.form("imap_fetch_form"):
                col_h, col_p = st.columns([3, 1])
                with col_h:
                    imap_host = st.text_input("IMAP Host", value=default_host, placeholder="imap.gmail.com")
                with col_p:
                    imap_port = st.number_input("Port", value=default_port, min_value=1, max_value=65535)
                imap_user = st.text_input("Email address", placeholder="you@gmail.com")
                imap_pass = st.text_input(
                    "App password",
                    type="password",
                    help=(
                        "Use an app-specific password, not your account password. "
                        "Gmail: myaccount.google.com/apppasswords. "
                        "Outlook: account.microsoft.com/security → App passwords."
                    ),
                )
                col_f, col_m = st.columns(2)
                with col_f:
                    subject_filter = st.text_input(
                        "Subject filter", value="invoice",
                        help="Fetch unread messages whose subject contains this string.",
                    )
                with col_m:
                    max_msgs = st.number_input("Max messages", value=10, min_value=1, max_value=50)
                mark_read = st.checkbox("Mark fetched messages as read", value=False)
                fetch_submitted = st.form_submit_button("Fetch from Mailbox", type="primary")

            if fetch_submitted:
                if not imap_host or not imap_user or not imap_pass:
                    st.error("Host, email address, and app password are all required.")
                else:
                    with st.spinner(f"Connecting to {imap_host}:{int(imap_port)} …"):
                        messages = imap_fetch_invoices(
                            host=imap_host,
                            port=int(imap_port),
                            username=imap_user,
                            password=imap_pass,
                            subject_filter=subject_filter,
                            max_messages=int(max_msgs),
                            mark_read=mark_read,
                        )
                    if not messages:
                        st.info(f"No unread messages matching '{subject_filter}' found in INBOX.")
                    else:
                        st.success(f"Fetched {len(messages)} message(s).")
                        for i, msg_result in enumerate(messages):
                            label = msg_result.get("subject") or f"Message {i + 1}"
                            with st.expander(f"📧 {label}", expanded=(i == 0)):
                                _show_eml_result(msg_result, uid_prefix=msg_result.get("uid", str(i)))

        else:  # Demo scenario
            demo_choice = st.selectbox(
                "Select demo scenario",
                [
                    "✅ CLEAN — Legitimate invoice email (no suspicious headers)",
                    "🚨 HIGH RISK — VEC attack (reply-to hijack + urgency keywords)",
                ],
                key="demo_eml_choice",
            )
            scenario_key = "clean" if "CLEAN" in demo_choice else "vec_attack"
            eml_bytes = make_demo_eml(scenario_key)

        if eml_bytes:
            result = ingest_eml(eml_bytes)
            _show_eml_result(result)

    # --- Tab 6: B/L Scanner ---
    with tab6:
        st.header("Bill of Lading Scanner")
        st.markdown(
            "> Upload a Bill of Lading image to extract all logistics identifiers "
            "and instantly verify the physical event against live AIS vessel data."
        )

        uploaded_bl = st.file_uploader(
            "Upload Bill of Lading (JPEG / PNG / WebP)",
            type=["jpg", "jpeg", "png", "webp"],
            key="bl_uploader",
        )

        if uploaded_bl:
            image_bytes = uploaded_bl.read()
            col_img, col_results = st.columns([1, 1], gap="medium")

            with col_img:
                st.image(image_bytes, caption=uploaded_bl.name, use_container_width=True)

            with col_results:
                if st.button("Scan & Validate B/L", type="primary", key="bl_scan_btn"):
                    # Step 1 — extract
                    with st.spinner("Extracting fields via LLaMA-4 Scout…"):
                        try:
                            bl = _extract_bl(image_bytes, st.session_state.groq_api_key)
                            st.session_state["bl_fields"] = bl
                        except Exception as e:
                            st.error(f"Extraction failed: {e}")
                            st.session_state.pop("bl_fields", None)
                            st.session_state.pop("bl_telemetry", None)

                    # Step 2 — telemetry (only if extraction succeeded)
                    if st.session_state.get("bl_fields"):
                        mt_key = None
                        try:
                            mt_key = st.secrets.get("MARINETRAFFIC_API_KEY")
                        except Exception:
                            pass
                        if not mt_key:
                            mt_key = os.getenv("MARINETRAFFIC_API_KEY")

                        ais_key = None
                        if not mt_key:
                            try:
                                ais_key = st.secrets.get("AISSTREAM_API_KEY")
                            except Exception:
                                pass
                            if not ais_key:
                                ais_key = os.getenv("AISSTREAM_API_KEY")

                        bl = st.session_state["bl_fields"]
                        cargo_raw = bl.get("cargo_description") or ""
                        payload = {
                            "mmsi": bl.get("mmsi") or "",
                            "imo": bl.get("imo") or "",
                            "discharge_port": bl.get("port_of_discharge") or "",
                            "invoice_date": bl.get("bl_date") or "",
                            "cargo_type": _map_cargo_type(cargo_raw),
                            "voyage_id": bl.get("voyage_number") or "",
                            "cargo_quantity_mt": bl.get("cargo_quantity_mt"),
                        }
                        with st.spinner("Checking vessel against AIS data…"):
                            st.session_state["bl_telemetry"] = telemetry_context_validation(
                                payload,
                                marinetraffic_api_key=mt_key,
                                aisstream_api_key=ais_key,
                            )

            # ── Results ──────────────────────────────────────────────────────
            if st.session_state.get("bl_fields"):
                bl = st.session_state["bl_fields"]

                st.divider()
                st.subheader("Extracted B/L Fields")

                fields = [
                    ("B/L Number",        bl.get("bl_number")),
                    ("Vessel",            bl.get("vessel_name")),
                    ("MMSI",              bl.get("mmsi")),
                    ("IMO",               bl.get("imo")),
                    ("Voyage",            bl.get("voyage_number")),
                    ("Port of Loading",   bl.get("port_of_loading")),
                    ("Port of Discharge", bl.get("port_of_discharge")),
                    ("B/L Date",          bl.get("bl_date")),
                    ("Cargo",             bl.get("cargo_description")),
                    ("Quantity (MT)",     bl.get("cargo_quantity_mt")),
                    ("Shipper",           bl.get("shipper")),
                    ("Consignee",         bl.get("consignee")),
                ]
                col_a, col_b = st.columns(2)
                for i, (label, value) in enumerate(fields):
                    with (col_a if i % 2 == 0 else col_b):
                        st.markdown(f"**{label}:** {value or '—'}")

            if st.session_state.get("bl_telemetry"):
                tel = st.session_state["bl_telemetry"]
                verdict = tel.get("verdict", "BLOCKED" if tel["is_tampered"] else "CLEAR")

                st.divider()
                st.subheader("Telemetry Verdict")

                if verdict == "BLOCKED":
                    st.error("🚨 BLOCKED — Fraud indicator detected")
                elif verdict == "REVIEW":
                    st.warning("⚠️ REVIEW — Could not fully verify against AIS data")
                else:
                    st.success("✅ CLEAR — Physical event confirmed")

                st.markdown(f"**Risk score:** `{tel['risk_score']}`")
                st.markdown(f"**Reason:** {tel['overall_reason']}")

                if tel.get("vessel_name"):
                    st.markdown(
                        f"**Vessel:** {tel['vessel_name']} &nbsp;|&nbsp; "
                        f"**Carrier:** {tel.get('carrier', '—')} &nbsp;|&nbsp; "
                        f"**Source:** `{tel.get('source', '—')}`",
                        unsafe_allow_html=True,
                    )

                st.subheader("Check Breakdown")
                for check in tel.get("checks", []):
                    icon = "✅" if check["status"] == "PASS" else ("⚠️" if check["status"] == "WARN" else "❌")
                    st.markdown(f"{icon} **`{check['field']}`** — {check['detail']}")

    # Batch processing status
    display_batch_status(st.session_state.invoices)

if __name__ == "__main__":
    enhanced_ui()