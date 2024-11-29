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
    try:
        mime_type = uploaded_file.type
        if not mime_type:
            raise ValueError("Cannot determine file MIME type.")
        myfile = genai.upload_file(uploaded_file, mime_type=mime_type)
    except Exception as e:
        raise ValueError(f"Failed to upload file: {e}")

    if myfile is None:
        raise ValueError("File upload failed!")

    model = genai.GenerativeModel(model_name)
    prompt = """
    ... (Prompt remains unchanged) ...
    """
    result = model.generate_content([myfile, prompt])
    result_text = result.text if hasattr(result, 'text') else result.choices[0].text

    try:
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

    if not os.path.exists(excel_file_path):
        with pd.ExcelWriter(excel_file_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name="Product Details", index=False)
    else:
        with pd.ExcelWriter(excel_file_path, engine='openpyxl', mode='a', if_sheet_exists='overlay') as writer:
            startrow = writer.sheets["Product Details"].max_row
            df.to_excel(writer, sheet_name="Product Details", index=False, header=False, startrow=startrow)

# Function 3: Process the invoice and update the Excel file
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

    # Get the directory of the uploaded file
    input_file_path = uploaded_file.name
    input_directory = os.path.dirname(input_file_path)
    output_directory = input_directory  # Set output folder same as input folder

    # Define output file path
    file_name = os.path.splitext(uploaded_file.name)[0] + "_output.xlsx"
    excel_file_path = os.path.join(output_directory, file_name)

    append_product_data_to_excel(products, excel_file_path)

    # Print full paths of input and output
    print(f"Input file path: {os.path.abspath(uploaded_file.name)}")
    print(f"Output file path: {os.path.abspath(excel_file_path)}")

    print(f"Invoice data processed and saved to {excel_file_path}.")

# Function 4: Streamlit interface for multiple file uploads
def upload_images_streamlit():
    st.title("Invoice Processing")

    uploaded_files = st.file_uploader(
        "Choose invoice images", type=["jpg", "jpeg", "png"], accept_multiple_files=True
    )

    if uploaded_files:
        for uploaded_file in uploaded_files:
            process_invoice(uploaded_file, output_directory="")  # Output directory will be determined dynamically.

        st.success(f"All invoices processed. Files saved in the respective input folders.")

if __name__ == "__main__":
    upload_images_streamlit()
