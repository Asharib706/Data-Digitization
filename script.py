import os
import json
import pandas as pd
from openpyxl import load_workbook
from datetime import datetime
import numpy as np
import tkinter as tk
from tkinter import filedialog
import google.generativeai as genai
from dotenv import load_dotenv
load_dotenv()


genai.configure(api_key=os.environ["API_KEY"])
# Function 1: Extract data using Gemini
def extract_invoice_data(image_path, model_name="gemini-1.5-flash-8b"):
    """
    Extracts data from an invoice image using the Gemini model.
    """
    # Upload image to Gemini
    myfile = genai.upload_file(image_path)
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
    result_text = result.text if hasattr(result, 'text') else result.choices[0].text

    # Debug: Print raw response from model
    print("Raw Response from Gemini Model:", result_text)

    try:
        # Parse result as JSON
        start_index = result_text.find('{')
        end_index = result_text.rfind('}') + 1
        cleaned_result = result_text[start_index:end_index]
        
        # Debug: Print cleaned result before attempting to load as JSON
        print("Cleaned Result for JSON Parsing:", cleaned_result)

        invoice_data = json.loads(cleaned_result)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON. Error: {e}")
        print(f"Raw response: {result_text}")
        return None
    
    return invoice_data

# Function 2: Append product data to the Product Details sheet
def append_product_data_to_excel(product_data, excel_file_path):
    """
    Appends extracted product data to the Product Details sheet in the Excel file without appending column names.
    """
    # Convert product data to DataFrame
    df = pd.DataFrame(product_data)
    df['invoice_date'] = pd.to_datetime(df['invoice_date'], format='%m/%d/%Y', errors='coerce')
    df['month_year'] = df['invoice_date'].dt.to_period('M')
    df['gst_amount'] = (df['total_price'] * df['gst%']) / 100

    threshold = 1e-3

    # Filter out small values before assigning
    df.loc[((df['unit_price'] * df['quantity']) > df['total_price']) & (df['discount'] == 0), 'discount'] = np.where(abs((df['unit_price'] * df['quantity']) - df['total_price']) < threshold, 0, (df['unit_price'] * df['quantity']) - df['total_price'])
    # Remove duplicate rows based on all columns
    df = df.drop_duplicates()
 
    # Reorder columns
    columns_order = [
        "invoice_date",
        "invoice_number",
        "store_name",
        "product_name",
        "unit_price",
        "quantity",
        "total_price",
        "discount",
        "gst%",
        "gst_amount",
        "month_year"
    ]
    df = df[columns_order]

    # Append to Excel without including column names
    if os.path.exists(excel_file_path):
        with pd.ExcelWriter(excel_file_path, engine='openpyxl', mode='a', if_sheet_exists='overlay') as writer:
            df.to_excel(writer, sheet_name="Product Details", index=False, header=False, 
                        startrow=writer.sheets["Product Details"].max_row)
    else:
        with pd.ExcelWriter(excel_file_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name="Product Details", index=False)

# Function 3: Generate summary from Product Details sheet
def generate_summary_from_product_details(excel_file_path):
    """
    Reads the Product Details sheet, generates a summary, and writes it to the Summary by Month sheet.
    """
    # Load the Product Details sheet
    df = pd.read_excel(excel_file_path, sheet_name="Product Details")
    # Generate summary grouped by month
    summary_df = df.groupby('month_year').agg({
        'quantity': 'sum',
        'total_price': 'sum',
        'discount': 'sum',
        'gst_amount': 'sum'
    }).reset_index()

    # Save summary to the Summary by Month sheet
    with pd.ExcelWriter(excel_file_path, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
        summary_df.to_excel(writer, sheet_name="Summary by Month", index=False)

# Function 4: Process the invoice and update the Excel file
def process_invoice(image_path, excel_file_path, model_name="gemini-1.5-flash-8b"):
    """
    Full pipeline: extracts invoice data, appends product details, and updates summary.
    """
    # Extract data using Gemini
    invoice_data = extract_invoice_data(image_path, model_name)

    if invoice_data is None:
        print("Failed to extract invoice data.")
        return

    # Prepare product data for Excel
    store_name = invoice_data["store_name"]
    invoice_number = invoice_data["invoice_number"]
    invoice_date = invoice_data["invoice_date"]

    products = invoice_data["data"]
    for product in products:
        product["store_name"] = store_name
        product["invoice_number"] = invoice_number
        product["invoice_date"] = invoice_date

    # Append product data to Excel
    append_product_data_to_excel(products, excel_file_path)

    # Generate summary from updated product details
    generate_summary_from_product_details(excel_file_path)

    print(f"Invoice data processed and appended to {excel_file_path}. Summary updated.")

# Function 5: GUI for Image Upload
def upload_image_gui():
    """
    Opens a dialog to select an image file and processes it.
    """
    root = tk.Tk()
    root.withdraw()  # Hide the root window

    # Open file dialog to select an image
    image_path = filedialog.askopenfilename(title="Select an Invoice Image", filetypes=[("Image files", "*.jpg;*.jpeg;*.png")])

    if image_path:
        print(f"Image selected: {image_path}")
        # Call the process_invoice function to handle the image
        excel_file_path = "output.xlsx"  # Specify the output file path
        process_invoice(image_path, excel_file_path)
    else:
        print("No image selected.")

# Main execution
if __name__ == "__main__":
    upload_image_gui()
