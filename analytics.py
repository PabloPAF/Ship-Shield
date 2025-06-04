import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np
from utils import InvoiceData

def analyze_invoices(invoices):
    """Display an analytics dashboard for processed invoices."""
    df = pd.DataFrame([inv["invoice"].dict() for inv in invoices])
    
    # Total Amount Distribution
    if "total_amount" in df and df["total_amount"].notna().any():
        st.subheader("Total Amount Distribution")
        fig = px.histogram(df, x="total_amount", nbins=20, title="Distribution of Total Amounts")
        st.plotly_chart(fig, use_container_width=True)
    
    # Tax vs. Subtotal Scatter
    if "subtotal" in df and "tax" in df and df[["subtotal", "tax"]].notna().any().all():
        st.subheader("Tax vs. Subtotal")
        fig = px.scatter(df, x="subtotal", y="tax", title="Tax vs. Subtotal", hover_data=["invoice_number"])
        st.plotly_chart(fig, use_container_width=True)
    
    # Currency Breakdown
    if "currency" in df and df["currency"].notna().any():
        st.subheader("Currency Breakdown")
        currency_counts = df["currency"].value_counts().reset_index()
        currency_counts.columns = ["Currency", "Count"]
        fig = px.pie(currency_counts, names="Currency", values="Count", title="Invoices by Currency")
        st.plotly_chart(fig, use_container_width=True)

def detect_anomalies(invoices):
    """Detect anomalies in invoice data using z-score."""
    st.subheader("Anomaly Detection")
    df = pd.DataFrame([inv["invoice"].dict() for inv in invoices])
    
    if "total_amount" in df and df["total_amount"].notna().any():
        totals = df["total_amount"].dropna()
        if len(totals) > 1:
            mean = totals.mean()
            std = totals.std()
            z_scores = (totals - mean) / std
            threshold = 2.5  # Standard z-score threshold for outliers
            anomalies = df.loc[totals.index][abs(z_scores) > threshold]
            
            if not anomalies.empty:
                st.warning("⚠️ Potential anomalies detected in total amounts:")
                st.dataframe(anomalies[["invoice_number", "total_amount", "currency"]])
            else:
                st.success("✅ No anomalies detected in total amounts.")
        else:
            st.info("Not enough data for anomaly detection.")
    else:
        st.info("No total amount data available for anomaly detection.")