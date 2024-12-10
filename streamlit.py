import streamlit as st
import os
import json
import bcrypt
from pymongo import MongoClient
from datetime import datetime
from bson.objectid import ObjectId
from dotenv import load_dotenv
import google.generativeai as genai
import tempfile
import pandas as pd
from io import BytesIO
from datetime import datetime
# Load environment variables
load_dotenv()

# Configure Gemini API
genai.configure(api_key=os.environ["API_KEY"])

# MongoDB Configuration
MONGO_URI = os.environ["MONGO_URI"]
DB_NAME = "invoice_db"
PRODUCT_COLLECTION = "product_details"
USER_COLLECTION = "users"

# MongoDB client setup
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
product_collection = db[PRODUCT_COLLECTION]
user_collection = db[USER_COLLECTION]

# Streamlit UI
st.title("Invoice and Product Management System")

# Session State for Login
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None

# Helper Functions
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

def verify_password(password, hashed_password):
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password)

def extract_invoice_data(image_bytes, model_name="gemini-1.5-flash-8b"):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
        temp_file.write(image_bytes)
        temp_file_path = temp_file.name

    try:
        myfile = genai.upload_file(temp_file_path)
        if not myfile:
            raise ValueError("File upload failed!")

        model = genai.GenerativeModel(model_name)
        prompt = """
Extract specific fields from a clear and non-blurry image if it represents an invoice or financial report. 
If the image is blurry, return an error message indicating that the image is unacceptable for processing. 
Focus on extracting the following information accurately and structuring it in the specified JSON format.

### Fields to Extract:

1. **General Information:**
   - **Vendor Name**: Extract the name of the vendor or the title of the invoice/receipt (if available). If missing, set to `None`.
   - **Invoice/Receipt Number**: Extract the unique identifier for the invoice or receipt. If missing, set to `None`.
   - **Invoice Date**: Extract the invoice date in the format `MM/DD/YYYY`. If unavailable, default to today's date.

2. **Item Details (for the invoice/receipt as a whole):**
   - **Sub-total**: Extract the subtotal amount from the invoice/receipt. if unavailable give Total Price.
   - "Tps":"Extract the Tps(Goods and Services Tax) from the reciept,if unavailable set to '0'.
   - "Tvq":"Extract the Tvq(Quebec Sales Tax) from the reciept,if unavailable set to '0'.
   -"Tax": total tax from the recipts (should be equal to the sum of Tps and Tvq),if unavailable set to '0'
   - **Total Price**: Extract the final total amount of the invoice/receipt. If unavailable, set to `0`.
   - **Discount**: Extract any discounts applied to the invoice/receipt total. If unavailable, default to `0`.

### Output Format:
The result should be structured in the following JSON format:

{
  "vendor_name": "value or None",
  "invoice_number": "value or None",
  "invoice_date": "MM/DD/YYYY or today's date", 

  "data": [
    {
      "sub_total": value or 0,
      "tps":value or 0,
      "tvq":value or 0,
      "tax": value or 0,
      "total_price": value or 0,
      "discount": value or 0
    }
  ]
}

### Notes:
- If the image is blurry, output: {"error": "The image is blurry and cannot be processed."}
- Ensure the extraction is accurate and adheres to the specified JSON format.
"""

        result = model.generate_content([myfile, prompt])
        result_text = result.text if hasattr(result, "text") else result.choices[0].text

        start_index = result_text.find("{")
        end_index = result_text.rfind("}") + 1
        invoice_data = json.loads(result_text[start_index:end_index])

        st.write("Extracted Invoice Data:", invoice_data)
        return invoice_data
    except Exception as e:
        st.error(f"Error during invoice extraction: {e}")
        return None
    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

def append_to_mongodb(invoice_data):
    try:
        if not invoice_data or "data" not in invoice_data:
            st.error("No data found in the invoice.")
            return

        vendor_name = invoice_data.get("vendor_name", None)
        invoice_number = invoice_data.get("invoice_number", None)
        invoice_date = invoice_data.get("invoice_date", None)

        if not vendor_name or not invoice_number or not invoice_date:
            raise ValueError("Missing essential invoice fields: 'vendor_name', 'invoice_number', or 'invoice_date'.")

        for item in invoice_data["data"]:
            if not isinstance(item, dict):
                raise ValueError(f"Invalid data format for item: {item}")

            item.update({"username": st.session_state.username})
            
            product_collection.update_one(
                {
                    "invoice_number": invoice_number,
                    "invoice_date": invoice_date,
                    "vendor_name": vendor_name,
                    "username": st.session_state.username,
                },
                {"$set": item},
                upsert=True
            )
    except Exception as e:
        st.error(f"An error occurred while appending data to MongoDB: {e}")

def generate_summary_from_mongodb(username):
    try:
        if not username:
            raise ValueError("Username is required to fetch data.")

        # Fetch all data for the specific user
        all_data = list(product_collection.find({"username": username}))
        if not all_data:
            st.warning("No data found for the specified user.")
            return None

        # Create a DataFrame from the retrieved data
        df = pd.DataFrame(all_data)
        if df.empty:
            st.warning("No data available to create a summary.")
            return None

        # Ensure required columns exist
        required_columns = ["invoice_date", "total_price", "sub_total",'tps','tvq',"tax", "discount"]
        for col in required_columns:
            if col not in df.columns:
                raise KeyError(f"Missing required field in the data: {col}")

        df["invoice_date"] = pd.to_datetime(df["invoice_date"], format="%m/%d/%Y", errors="coerce")
        df["year-month"] = df["invoice_date"].dt.to_period("M")


        # Grouped summary
        summary_df = df.groupby(["year-month", "vendor_name"], dropna=False).agg({
            "sub_total": "sum",
            'tps':'sum',
            'tvq':'sum',
            "tax": "sum",
            "total_price": "sum",
            "discount": "sum",
        }).reset_index()

        # Add roll-up levels
        summary_df = pd.concat([
            summary_df,
            summary_df.groupby("year-month").agg({
                "sub_total": "sum",
                'tps':'sum',
                'tvq':'sum',
                "tax": "sum",
                "total_price": "sum",
                "discount": "sum"
            }).reset_index().assign(vendor_name="Total for Month"),
            pd.DataFrame(summary_df.agg({
                "sub_total": "sum",
                'tps':'sum',
                'tvq':'sum',
                "tax": "sum",

                "total_price": "sum",
                "discount": "sum"
            }).to_dict(), index=[0]).assign(
                year_month="Grand Total",
                vendor_name="All Vendors"
            )
        ])

        # Adjust column order for clarity
        columns_order = ["year-month", "vendor_name", "sub_total", "tax","tps", "tvq","total_price", "discount"]
        summary_df = summary_df[columns_order]

        return df, summary_df
    except KeyError as ke:
        st.error(f"Data inconsistency error: {ke}")
    except ValueError as ve:
        st.error(f"Validation error: {ve}")
    except Exception as e:
        st.error(f"An unexpected error occurred: {e}")

# User Authentication
def login():
    username = st.text_input("Username", key="login_username")
    password = st.text_input("Password", type="password", key="login_password")
    if st.button("Login"):
        user = user_collection.find_one({"username": username})
        if user and verify_password(password, user["password"]):
            st.success("Login successful!")
            st.session_state.logged_in = True
            st.session_state.username = username
        else:
            st.error("Invalid username or password.")

def signup():
    username = st.text_input("Username", key="signup_username")
    password = st.text_input("Password", type="password", key="signup_password")
    if st.button("Sign Up"):
        if user_collection.find_one({"username": username}):
            st.error("Username already exists!")
        else:
            hashed_password = hash_password(password)
            user_collection.insert_one({"username": username, "password": hashed_password})
            st.success("Signup successful! You can now log in.")

# Product Management
def add_product():
    st.header("Add Product")

    # Input fields for invoice and product details
    invoice_number = st.text_input("Invoice Number")
    user_date_input = st.text_input("Invoice Date (Format MM/DD/YYYY, optional)")
    if user_date_input:
        try:
            # Validate the input date
            invoice_date = datetime.strptime(user_date_input, "%m/%d/%Y").strftime("%m/%d/%Y")
        except ValueError:
            st.error("Invalid date format. Please use MM/DD/YYYY.")
            return
    else:
        # Default to today's date
        today = datetime.now()
        invoice_date = today.strftime("%m/%d/%Y")

    vendor_name = st.text_input("Vendor Name")
    sub_total = st.number_input("Sub Total", min_value=0.0)
    tps=st.number_input("TPS (Goods and Services Tax)", min_value=0.0)
    tvq=st.number_input("TQV (Quebec Sales Tax)", min_value=0.0)
    tax=tps+tvq

    total_price = st.number_input("Total Price", min_value=0.0)
    discount = st.number_input("Discount", min_value=0.0)



    if st.button("Add Product"):
        # Check if user is logged in
        if st.session_state.get("logged_in", False):
            try:
                # Create the product dictionary
                product = {
                    "username": st.session_state.username,
                    "invoice_number": invoice_number,
                    "invoice_date": invoice_date,
                    "vendor_name": vendor_name,
                    
                    "sub_total":sub_total,
                    "tps":tps,
                    "tvq":tvq,
                    "tax":tax,
                    "total_price": total_price,
                    "discount": discount,
                    
                }

                # Insert product into the database
                product_collection.insert_one(product)
                st.success("Product added successfully!")
            except Exception as e:
                st.error(f"An error occurred while adding the product: {e}")
        else:
            st.error("You must be logged in to add products.")

def delete_product():
    st.header("Delete Product")
    product_id = st.text_input("Enter Product Object ID")
    if st.button("Delete Product"):
        try:
            result = product_collection.delete_one({"_id": ObjectId(product_id), "username": st.session_state.username})
            if result.deleted_count > 0:
                st.success("Product deleted successfully!")
            else:
                st.error("Product not found or you don't have permission to delete it.")
        except Exception as e:
            st.error(f"Error deleting product: {e}")

# Main Application
if not st.session_state.logged_in:
    st.sidebar.title("Authentication")
    auth_mode = st.sidebar.radio("Choose an action:", ["Login", "Sign Up"])
    if auth_mode == "Login":
        login()
    else:
        signup()
else:
    st.sidebar.title(f"Welcome, {st.session_state.username}!")
    if st.sidebar.button("Log Out"):
        st.session_state.logged_in = False
        st.session_state.username = None
        st.success("Logged out successfully!")

    st.sidebar.header("Navigation")
    options = ["Upload Invoice", "Add Product","Generate Summary", "Delete Product",]
    choice = st.sidebar.radio("Go to:", options)

    if choice == "Upload Invoice":
        uploaded_files = st.file_uploader("Upload Invoice Images", accept_multiple_files=True, type=["jpg", "png", "jpeg"])
        if uploaded_files:
            for file in uploaded_files:
                image_bytes = file.read()
                invoice_data = extract_invoice_data(image_bytes)
                if invoice_data:
                    append_to_mongodb(invoice_data)
            st.success("Invoices processed successfully!")
    elif choice == "Add Product":
        add_product()
    elif choice == "Generate Summary":
        st.header("Generate Summary")
        download_button = st.button("Generate and Download Summary")
    
        if download_button:
            product_data,summary_df = generate_summary_from_mongodb(st.session_state.username)

            if summary_df is not None:
                # Prepare the output buffer
                output_buffer = BytesIO()

                # Use ExcelWriter to write multiple sheets
                with pd.ExcelWriter(output_buffer, engine="openpyxl") as writer:
                    # Save detailed data to a separate sheet
                    
                        # Drop MongoDB-specific fields for cleaner output if needed
                    product_data.to_excel(writer, sheet_name="Invoice Details", index=False)

                    # Save summary data to another sheet
                    summary_df.to_excel(writer, sheet_name="Summary by Month", index=False)

                # Move the buffer to the beginning
                output_buffer.seek(0)

                # Create a download button for the Excel file
                st.download_button(
                    label="Download Output File",
                    data=output_buffer,
                    file_name="summary_output.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.error("No data available to generate the summary.")

    elif choice == "Delete Product":
        delete_product()

