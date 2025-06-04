
import streamlit as st
import json
import base64
import argparse
import pandas as pd
from utils import InvoiceData, GroqClient, LineItem
from utils import (
    process_image_upload,
    process_image_url,
    display_image_preview,
    setup_page,
    select_input_method,
    show_extraction_button,
    display_results,
    display_error,
    run_chatbot,
    edit_invoice_data,
    export_to_csv,
    preprocess_image,
)

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Run the Streamlit app.')
    parser.add_argument('--environment', 
                        type=str, 
                        choices=['local', 'cloud'], 
                        default='cloud',
                        help='Specify the environment: "local" or "cloud".')
    args = parser.parse_args()
    
    if args.environment == 'cloud':
        # Access secret values
        groq_api_key = st.secrets["GROQ_API_KEY"]
    else:
        from dotenv import load_dotenv
        import os
        load_dotenv()
        # Access secret values
        groq_api_key = os.getenv("GROQ_API_KEY")    

    # Store secrets in session_state
    if "groq_api_key" not in st.session_state:
        st.session_state.groq_api_key = groq_api_key

    setup_page()
    input_method = select_input_method()
    
    image_bytes = None
    image_url = None
    mime_type = "image/jpeg"
    
    if input_method == "Upload Image 📤":
        uploaded_file = st.file_uploader("Upload an invoice image", type=["png", "jpg", "jpeg"])
        image_bytes, mime_type = process_image_upload(uploaded_file)
        if image_bytes:
            # Preprocess image to improve OCR accuracy
            try:
                image_bytes = preprocess_image(image_bytes)
            except Exception as e:
                display_error(f"Image preprocessing failed: {str(e)}")
    else:
        image_url = st.text_input("Enter image URL:")
        if image_url:
            try:
                image_bytes = process_image_url(image_url)
                if image_bytes:
                    image_bytes = preprocess_image(image_bytes)
            except ValueError as e:
                display_error(str(e))
    
    # Multi-language support
    st.subheader("Language Selection")
    language = st.selectbox("Select invoice language for better OCR accuracy:", 
                           ["English", "Spanish", "French", "German", "Other"])
    
    if image_bytes:
        col1, col2 = st.columns([1, 2])
        with col1:
            st.subheader("Invoice Image")
            display_image_preview(image_bytes)
        
        with col2:
            st.subheader("Extracted Invoice Fields")
            if show_extraction_button():
                with st.spinner("Extracting data using LLaMA 4..."):
                    try:
                        groq_client = GroqClient(api_key=st.session_state.groq_api_key)
                        
                        prompt = f"""
                        You are an intelligent OCR extraction agent capable of understanding and processing invoices in {language}.
                        Extract all relevant information from the provided invoice image in structured JSON format.
                        The JSON object must follow this schema: {json.dumps(InvoiceData.model_json_schema(), indent=2)}.
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
                        Example:
                        {{
                          "data": {{
                            "invoice_number": "INV123",
                            "invoice_date": "2025-01-01",
                            ...
                          }},
                          "confidence_scores": {{
                            "invoice_number": 0.95,
                            "invoice_date": 0.90,
                            ...
                          }}
                        }}
                        """
                        
                        if input_method == "Upload Image 📤":
                            base64_image = base64.b64encode(image_bytes).decode("utf-8")
                            image_content = {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
                            }
                        else:
                            image_content = {
                                "type": "image_url",
                                "image_url": {"url": image_url}
                            }
                        
                        # Retry logic for extraction
                        max_retries = 2
                        for attempt in range(max_retries):
                            try:
                                extracted_data = groq_client.extract_invoice_data(
                                    prompt, 
                                    image_content,
                                    model="meta-llama/llama-4-scout-17b-16e-instruct"
                                )
                                # Log raw response for debugging
                                st.write("Raw API Response:")
                                st.json(extracted_data)
                                
                                invoice = InvoiceData(**extracted_data.get("data", {}))
                                confidence_scores = extracted_data.get("confidence_scores", {})
                                
                                # Check if all fields are null
                                if all(value is None for value in extracted_data.get("data", {}).values()):
                                    st.warning(f"Attempt {attempt + 1}: No data extracted. Retrying..." if attempt < max_retries - 1 else "All attempts failed to extract data.")
                                    continue
                                
                                display_results(invoice)
                                st.session_state.invoice_data = invoice
                                st.session_state.confidence_scores = confidence_scores
                                
                                # Display confidence scores
                                st.subheader("Confidence Scores")
                                st.json(confidence_scores)
                                break
                            
                            except Exception as e:
                                if attempt < max_retries - 1:
                                    st.warning(f"Attempt {attempt + 1} failed: {str(e)}. Retrying...")
                                    continue
                                display_error(f"Failed to parse invoice after {max_retries} attempts: {str(e)}")
                    
                    except Exception as e:
                        display_error(f"Failed to parse invoice: {str(e)}")
            
            # Interactive data correction
            if "invoice_data" in st.session_state:
                st.subheader("Edit Extracted Data")
                edited_invoice = edit_invoice_data(st.session_state.invoice_data)
                if edited_invoice:
                    st.session_state.invoice_data = edited_invoice
                    st.success("✅ Data updated successfully!")
                    display_results(edited_invoice)
                
                # Export to CSV
                st.subheader("Export Data")
                if st.button("Download as CSV"):
                    csv_data = export_to_csv(st.session_state.invoice_data)
                    st.download_button(
                        label="Download CSV",
                        data=csv_data,
                        file_name="invoice_data.csv",
                        mime="text/csv",
                    )
    
    # Chatbot section
    st.subheader("Invoice Assistant Chatbot")
    run_chatbot()

if __name__ == "__main__":
    main()
