import json
import base64
import streamlit as st
import requests
from PIL import Image, ImageEnhance
from io import BytesIO
from typing import List, Optional
from pydantic import BaseModel, Field
from groq import Groq
import pandas as pd 
# ---------------------------
# Data Models
# ---------------------------

class LineItem(BaseModel):
    description: Optional[str] = Field(
        None, description="A brief description of the product or service provided."
    )
    quantity: Optional[float] = Field(
        None, description="The number of units of the product or service."
    )
    unit_price: Optional[float] = Field(
        None, description="The price per unit of the product or service."
    )
    total_price: Optional[float] = Field(
        None, description="The total price for the line item, calculated as quantity × unit price."
    )

class InvoiceData(BaseModel):
    invoice_number: Optional[str] = Field(
        None, description="The unique identifier or reference number of the invoice."
    )
    invoice_date: Optional[str] = Field(
        None, description="The date when the invoice was issued."
    )
    due_date: Optional[str] = Field(
        None, description="The payment due date."
    )
    billing_address: Optional[str] = Field(
        None, description="The address of the customer who is being billed."
    )
    shipping_address: Optional[str] = Field(
        None, description="The address where the goods/services are to be delivered."
    )
    vendor_name: Optional[str] = Field(
        None, description="The name of the company or individual issuing the invoice."
    )
    customer_name: Optional[str] = Field(
        None, description="The name of the person or organization being billed."
    )
    line_items: Optional[List[LineItem]] = Field(
        None, description="A list of items described in the invoice."
    )
    subtotal: Optional[float] = Field(
        None, description="The sum of all line item totals before taxes or additional fees."
    )
    tax: Optional[float] = Field(
        None, description="The tax amount applied to the subtotal."
    )
    total_amount: Optional[float] = Field(
        None, description="The final total to be paid including subtotal and taxes."
    )
    currency: Optional[str] = Field(
        None, description="The currency in which the invoice is issued (e.g., USD, EUR)."
    )

# -----------------------------------
# LLaMA Client Wrapper using Groq API
# -----------------------------------

class GroqClient:
    def __init__(self, api_key):
        self.client = Groq(api_key=api_key)
    
    def extract_invoice_data(self, prompt, image_content, model="meta-llama/llama-4-scout-17b-16e-instruct"):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                image_content
            ]
        }]
        
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.4,
            max_completion_tokens=1024,
            stream=False,
            response_format={"type": "json_object"},
        )
        
        return json.loads(response.choices[0].message.content)
    
    def run_chatbot_query(self, prompt, model="meta-llama/llama-4-scout-17b-16e-instruct"):
        """Handle text-only chatbot queries."""
        messages = [{
            "role": "user",
            "content": prompt  # Direct string for text-only queries
        }]
        
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,  # Higher for conversational responses
            max_tokens=512,  # Adjusted for text-based responses
            stream=False,
        )
        
        return response.choices[0].message.content

# ---------------------------
# Image Handling Utilities
# ---------------------------

def process_image_upload(uploaded_file):
    if not uploaded_file:
        return None, None
    image_bytes = uploaded_file.read()
    suffix = uploaded_file.name.split(".")[-1].lower()
    mime_type = "image/jpeg" if suffix in ("jpg", "jpeg") else "image/png"
    return image_bytes, mime_type

def process_image_url(image_url):
    if not image_url:
        return None
    try:
        response = requests.get(image_url)
        response.raise_for_status()
        return response.content
    except Exception as e:
        raise ValueError(f"Error loading image from URL: {str(e)}")

def display_image_preview(image_bytes):
    try:
        image = Image.open(BytesIO(image_bytes))
        st.image(image)
    except Exception as e:
        st.error(f"Error displaying image: {str(e)}")

def preprocess_image(image_bytes: bytes) -> bytes:
    """Preprocess the image to improve OCR accuracy."""
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        
        # Enhance contrast
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)
        
        # Resize image to a reasonable size (e.g., max 1024px width)
        max_size = 1024
        image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        
        # Convert back to bytes
        output = BytesIO()
        image.save(output, format="JPEG", quality=95)
        return output.getvalue()
    except Exception as e:
        raise ValueError(f"Image preprocessing error: {str(e)}")

# ---------------------------
# Streamlit UI Functions
# ---------------------------

def setup_page():
    st.set_page_config(page_title="Invoice OCR with LLaMA 4", layout="wide")
    st.title("🧾 Smart Invoice Parser")

def select_input_method():
    return st.radio("Select input method: 📸", 
                   ["Upload Image 📤", "Image URL 🌐"])

def show_extraction_button():
    return st.button("Extract Invoice Data")

def display_results(invoice_data):
    st.success("✅ Data extracted successfully!")
    st.json(invoice_data.dict())

def display_error(message):
    st.error(f"❌ {message}")

def run_chatbot():
    """Run a Grok 3-powered chatbot to assist with invoice queries."""
    if "groq_api_key" not in st.session_state:
        st.error("API key not found. Please configure the environment.")
        return
    
    groq_client = GroqClient(api_key=st.session_state.groq_api_key)
    
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    
    # Display chat history
    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # User input
    user_input = st.chat_input("Ask about the invoice or OCR process...")
    if user_input:
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        
        # Prepare context with extracted invoice data
        context = ""
        if "invoice_data" in st.session_state:
            context = f"Extracted invoice data: {json.dumps(st.session_state.invoice_data.dict(), indent=2)}"
        
        prompt = f"""
        You are an AI assistant specialized in invoice processing. Answer the user's question: '{user_input}'.
        If relevant, use the extracted invoice data: {context}.
        Provide concise, helpful responses in a conversational tone.
        """
        
        try:
            response_text = groq_client.run_chatbot_query(prompt)
            st.session_state.chat_history.append({"role": "assistant", "content": response_text})
            
            with st.chat_message("assistant"):
                st.markdown(response_text)
        
        except Exception as e:
            st.error(f"Chatbot error: {str(e)}")

def edit_invoice_data(invoice_data: InvoiceData) -> Optional[InvoiceData]:
    """Allow users to edit extracted invoice data."""
    edited_data = invoice_data.dict()
    
    with st.form("edit_invoice_form"):
        edited_data["invoice_number"] = st.text_input("Invoice Number", value=edited_data.get("invoice_number", "") or "")
        edited_data["invoice_date"] = st.text_input("Invoice Date", value=edited_data.get("invoice_date", "") or "")
        edited_data["due_date"] = st.text_input("Due Date", value=edited_data.get("due_date", "") or "")
        edited_data["billing_address"] = st.text_area("Billing Address", value=edited_data.get("billing_address", "") or "")
        edited_data["shipping_address"] = st.text_area("Shipping Address", value=edited_data.get("shipping_address", "") or "")
        edited_data["vendor_name"] = st.text_input("Vendor Name", value=edited_data.get("vendor_name", "") or "")
        edited_data["customer_name"] = st.text_input("Customer Name", value=edited_data.get("customer_name", "") or "")
        edited_data["subtotal"] = st.number_input("Subtotal", value=edited_data.get("subtotal", 0.0) or 0.0, step=0.01)
        edited_data["tax"] = st.number_input("Tax", value=edited_data.get("tax", 0.0) or 0.0, step=0.01)
        edited_data["total_amount"] = st.number_input("Total Amount", value=edited_data.get("total_amount", 0.0) or 0.0, step=0.01)
        edited_data["currency"] = st.text_input("Currency", value=edited_data.get("currency", "") or "")
        
        # Line items editing
        line_items = edited_data.get("line_items", []) or []
        edited_line_items = []
        for i, item in enumerate(line_items):
            st.subheader(f"Line Item {i+1}")
            item_description = st.text_input(f"Description {i+1}", value=item.get("description", "") or "", key=f"description_{i}")
            item_quantity = st.number_input(f"Quantity {i+1}", value=item.get("quantity", 0.0) or 0.0, step=1.0, key=f"quantity_{i}")
            item_unit_price = st.number_input(f"Unit Price {i+1}", value=item.get("unit_price", 0.0) or 0.0, step=0.01, key=f"unit_price_{i}")
            item_total_price = st.number_input(f"Total Price {i+1}", value=item.get("total_price", 0.0) or 0.0, step=0.01, key=f"total_price_{i}")
            edited_line_items.append({
                "description": item_description,
                "quantity": item_quantity,
                "unit_price": item_unit_price,
                "total_price": item_total_price
            })
        edited_data["line_items"] = edited_line_items
        
        submit = st.form_submit_button("Save Changes")
        if submit:
            try:
                return InvoiceData(**edited_data)
            except Exception as e:
                st.error(f"Error saving changes: {str(e)}")
                return None
    return None

from typing import List

def export_to_csv(invoice_data_list: List[InvoiceData]) -> str:
    """Export a list of invoice data to CSV format."""
    if not invoice_data_list:
        return pd.DataFrame().to_csv(index=False)
    
    all_flat_data = []
    for invoice_data in invoice_data_list:
        data = invoice_data.dict()
        flat_data = {
            "invoice_number": data.get("invoice_number"),
            "invoice_date": data.get("invoice_date"),
            "due_date": data.get("due_date"),
            "billing_address": data.get("billing_address"),
            "shipping_address": data.get("shipping_address"),
            "vendor_name": data.get("vendor_name"),
            "customer_name": data.get("customer_name"),
            "subtotal": data.get("subtotal"),
            "tax": data.get("tax"),
            "total_amount": data.get("total_amount"),
            "currency": data.get("currency"),
        }
        
        # Handle line items (up to a reasonable limit, e.g., 10 items)
        line_items = data.get("line_items", []) or []
        for i, item in enumerate(line_items[:10], 1):  # Limit to 10 to avoid excessive columns
            flat_data.update({
                f"line_item_{i}_description": item.get("description"),
                f"line_item_{i}_quantity": item.get("quantity"),
                f"line_item_{i}_unit_price": item.get("unit_price"),
                f"line_item_{i}_total_price": item.get("total_price"),
            })
        
        all_flat_data.append(flat_data)
    
    df = pd.DataFrame(all_flat_data)
    return df.to_csv(index=False)
    
