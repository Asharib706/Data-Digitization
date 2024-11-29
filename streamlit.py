import os
import json
import pandas as pd
from openpyxl import load_workbook
import google.generativeai as genai
from dotenv import load_dotenv
import streamlit as st

load_dotenv()

genai.configure(api_key=os.environ["API_KEY"])

# Function 1: Extract data using Gemini
def extract_invoice_data(uploaded_file, model_name="gemini-1.5-flash-8b"):
    """
    Extracts data from an invoice image using the Gemini model.
    """
    try:
        # Determine the MIME type of the uploaded file
        mime_type = uploaded_file.type  # Get MIME type from Streamlit file object
        if not mime_type:
            raise ValueError("Cannot determine file MIME type.")

        # Upload the image file directly
        myfile = genai.upload_file(uploaded_file, mime_type=mime_type)
    except Exception as e:
        raise ValueError(f"Failed to upload file: {e}")

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
   - **Quantity**: Quantity of product. If unavailable, default to `0`. Can be float value.
   - **Total Price**: The total price for the item (calculated as `unit_price × quantity` if not explicitly provided). If unavailable, default to `0`.
   - **Discount**: Any discounts applied to the item or total. If unavailable, default to `0`.
   - **GST%**: The GST percentage applied to each item or given in total. If unavailable, default to `0`.

   Don't extract the total of the invoice, just the individual products.
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
    result_text = result.text if hasattr(result, 'text') else result.choices[0].text

    try:
        # Parse result as JSON
        start_index = result_text.find('{')
        end_index = result_text.rfind('}') + 1
        cleaned_result = result_text[start_index:end_index]

        invoice_data = json.loads(cleaned_result)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON. Error: {e}")
        print(f"Raw response: {result_text}")
        return None
    
    return invoice_data

# Function 2: Append product data to the Product Details sheet
def append_product_data_to_excel(product_data, excel_file_path):
    df = pd.DataFrame(product_data)
    df['invoice_date'] = pd.to_datetime(df['invoice_date'], format='%m/%d/%Y', errors='coerce')
    df['month_year'] = df['invoice_date'].dt.to_period('M')
    df['gst_amount'] = (df['total_price'] * df['gst%']) / 100

    # Check if the file exists
    if not os.path.exists(excel_file_path):
        # If the file doesn't exist, create it and write the DataFrame with headers
        with pd.ExcelWriter(excel_file_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name="Product Details", index=False)
    else:
        # If the file exists, append data without headers
        with pd.ExcelWriter(excel_file_path, engine='openpyxl', mode='a', if_sheet_exists='overlay') as writer:
            startrow = writer.sheets["Product Details"].max_row
            df.to_excel(writer, sheet_name="Product Details", index=False, header=False, startrow=startrow)

# Function 3: Process the invoice and update the Excel file
def process_invoice(uploaded_file, output_directory, model_name="gemini-1.5-flash-8b"):
    invoice_data = extract_invoice_data(uploaded_file, model_name)

    if invoice_data is None:
        print("Failed to extract invoice data.")
        return

    store_name = invoice_data["store_name"]
    invoice_number = invoice_data["invoice_number"]
    invoice_date = invoice_data["invoice_date"]

    products = invoice_data["data"]
    for product in products:
        product["store_name"] = store_name
        product["invoice_number"] = invoice_number
        product["invoice_date"] = invoice_date

    # Define output file path
    file_name = os.path.splitext(uploaded_file.name)[0] + "_output.xlsx"
    excel_file_path = os.path.join(output_directory, file_name)

    append_product_data_to_excel(products, excel_file_path)

    print(f"Invoice data processed and appended to {excel_file_path}. Summary updated.")

# Function 4: Streamlit interface for multiple file uploads
def upload_images_streamlit():
    st.title("Batch Invoice Processing with Gemini")

    # Upload multiple image files
    uploaded_files = st.file_uploader(
        "Choose invoice images", type=["jpg", "jpeg", "png"], accept_multiple_files=True
    )

    if uploaded_files:
        for uploaded_file in uploaded_files:
            # Get the directory of the uploaded file
            output_directory = os.path.dirname(uploaded_file.name)
            
            # Process each invoice
            process_invoice(uploaded_file, output_directory)

        st.success("All invoices processed and saved in their respective directories.")

if __name__ == "__main__":
    upload_images_streamlit()
