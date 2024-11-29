import streamlit as st
import pandas as pd
import os
import json
from pymongo import MongoClient
from datetime import datetime
from openpyxl import load_workbook
import numpy as np
from io import BytesIO
import google.generativeai as genai
import tempfile
from dotenv import load_dotenv
load_dotenv()
# Configure Gemini API
genai.configure(api_key=os.environ["API_KEY"])

# MongoDB Configuration
MONGO_URI = os.environ["MONGO_URI"]
DB_NAME = "invoice_db"
COLLECTION_NAME = "product_details"

# MongoDB client setup
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

# Streamlit UI
st.title("Invoice Data Processor with Streamlit")
st.sidebar.header("Upload Files")
uploaded_files = st.sidebar.file_uploader("Upload invoice images", accept_multiple_files=True, type=["jpg", "png", "jpeg"])
download_button = st.button("Generate and Download Summary")

# Function: Extract data using Gemini
def extract_invoice_data(image_bytes, model_name="gemini-1.5-flash-8b"):
    """
    Extracts data from an invoice image using the Gemini model.
    """
    # Save the image bytes to a temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
        temp_file.write(image_bytes)
        temp_file_path = temp_file.name  # Get the file path

    try:
        # Upload the temporary file to Gemini
        myfile = genai.upload_file(temp_file_path)
        if myfile is None:
            raise ValueError("File upload failed!")

        # Initialize model
        model = genai.GenerativeModel(model_name)

        # Define the prompt for data extraction
        prompt = """
Extract the following fields from the given image if it represents an invoice or financial report:

### Fields to Extract:
1. **General Information:**
   - **Store/Invoice Name**: The name of the store or invoice title (if available).
   - **Invoice/Receipt Number**: Unique identifier for the invoice or receipt.
   - **Invoice Date**: The date of the invoice in the format MM/DD/YYYY. If the date is missing, use today’s date.

2. **Item Details (for each product or service):**
   - **Product/Item Name**: The name of the product or item. If unavailable, use the invoice number as the product name.
   - **Unit Price**: Price of a single unit of the product. If unavailable, default to `0`.
   - **Quantity**: Quantity of product. If unavailable, default to `0`. can be float value
   - **Total Price**: The total price for the item (calculated as `unit_price × quantity` if not explicitly provided). If unavailable, default to `0`.
   - **Discount**: Any discounts applied to the item or total. If unavailable, default to `0`.
   - **GST%**: The GST percentage applied to each item or given in total. If unavailable, default to `0`

   Dont extract the total of the invoice just the individual products
### Output Format:

{
  "store_name": "value or None",
  "invoice_number": "value or None",
  "invoice_date": "MM/DD/YYYY or today's date", 

  "data": [
    {
      "product_name": "value or invoice_number",
      "unit_price": value or 0,
      "quantity": value or 0,
      "total_price": value or 0,
      "discount": value or 0,
      "gst%": value or 0
    }
    ...
  ]
}

Ensure the output is in the specified JSON format for consistency and ease of processing.
"""

        # Get extraction result
        result = model.generate_content([myfile, prompt])
        result_text = result.text if hasattr(result, "text") else result.choices[0].text

        # Parse the JSON response
        start_index = result_text.find("{")
        end_index = result_text.rfind("}") + 1
        cleaned_result = result_text[start_index:end_index]

        invoice_data = json.loads(cleaned_result)

        # Debugging: Print extracted data to Streamlit
        st.write("Extracted Invoice Data:", invoice_data)

        return invoice_data
    except Exception as e:
        print(f"Error during invoice extraction: {e}")
        return None
    finally:
        # Ensure the temporary file is deleted
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

# Function: Append product data to MongoDB
def append_to_mongodb(invoice_data):
    """
    Appends invoice data to MongoDB. Updates existing entries or inserts new ones.
    """
    if not invoice_data or "data" not in invoice_data:
        print("No product data found in the invoice.")
        return

    # Extract general information
    store_name = invoice_data.get("store_name", None)
    invoice_number = invoice_data.get("invoice_number", None)
    invoice_date = invoice_data.get("invoice_date", None)
    invoice_data["store_name"] = store_name
    invoice_data["invoice_number"] = invoice_number
    invoice_data["invoice_date"] = invoice_date

    # Check and process product items
    for product in invoice_data["data"]:
        # Add general invoice details to each product
        
        # Update or insert product into MongoDB
        collection.update_one(
            {
                "invoice_number": invoice_number,
                "product_name": product["product_name"],
            },
            {
                "$set": product,
            },
            upsert=True
        )

# Function: Generate summary from MongoDB
def generate_summary_from_mongodb():
    all_data = list(collection.find())
    if not all_data:
        return None

    df = pd.DataFrame(all_data)
    if df.empty:
        return None

    df["invoice_date"] = pd.to_datetime(df["invoice_date"], format="%m/%d/%Y", errors="coerce")
    df["month_year"] = df["invoice_date"].dt.to_period("M")
    df["gst_amount"] = (df["total_price"] * df["gst%"]) / 100

    summary_df = df.groupby("month_year").agg({
        "quantity": "sum",
        "total_price": "sum",
        "discount": "sum",
        "gst_amount": "sum"
    }).reset_index()

    return summary_df

# Main Processing
if uploaded_files:
    all_products = []

    for uploaded_file in uploaded_files:
        # Read file as bytes for Gemini API
        image_bytes = uploaded_file.read()
        invoice_data = extract_invoice_data(image_bytes)

        if invoice_data:
            store_name = invoice_data["store_name"]
            invoice_number = invoice_data["invoice_number"]
            invoice_date = invoice_data["invoice_date"]

            for product in invoice_data["data"]:
                product.update({
                    "store_name": store_name,
                    "invoice_number": invoice_number,
                    "invoice_date": invoice_date
                })

            all_products.extend(invoice_data["data"])

    # Append extracted data to MongoDB
    append_to_mongodb({"data": all_products})
    st.success("Invoice data processed and stored in MongoDB!")

# Generate and download output
if download_button:
    summary_df = generate_summary_from_mongodb()

    if summary_df is not None:
        output_buffer = BytesIO()

        with pd.ExcelWriter(output_buffer, engine="openpyxl") as writer:
            # Save detailed data
            product_df = pd.DataFrame(list(collection.find()))
            product_df.to_excel(writer, sheet_name="Product Details", index=False)

            # Save summary data
            summary_df.to_excel(writer, sheet_name="Summary by Month", index=False)

        output_buffer.seek(0)
        st.download_button(
            label="Download Output File",
            data=output_buffer,
            file_name="output.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.error("No data available to generate the summary.")
