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
st.title("An Automated System for Digitization and Analysis of Financial Documents")

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
Extract the following fields from the given image if it represents an invoice or financial report :
Note : Dont accept the blurry images

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
    if not invoice_data or "data" not in invoice_data:
        st.error("No product data found in the invoice.")
        return
    
    store_name = invoice_data.get("store_name", None)
    invoice_number = invoice_data.get("invoice_number", None)
    invoice_date = invoice_data.get("invoice_date", None)
    invoice_data["store_name"] = store_name
    invoice_data["invoice_number"] = invoice_number
    invoice_data["invoice_date"] = invoice_date
    for product in invoice_data["data"]:
        product.update({
            "username": st.session_state.username,
            
        })
        product_collection.update_one(
            {"invoice_number": invoice_number,
             "invoice_date": invoice_date,
             "store_name":store_name,
             "product_name": product["product_name"], 
             "username": st.session_state.username},
            {"$set": product},
            upsert=True
        )
def generate_summary_from_mongodb(username):
    # Fetch all data for the specific user
    all_data = list(product_collection.find({"username": username}))
    
    if not all_data:
        return None

    # Create a DataFrame from the retrieved data
    df = pd.DataFrame(all_data)
    if df.empty:
        return None

    # Ensure "invoice_date" is in the correct format
    df["invoice_date"] = pd.to_datetime(df["invoice_date"], format="%m/%d/%Y", errors="coerce")
    df["year-month"] = df["invoice_date"].dt.to_period("M")
    
    # Calculate GST amount
    df["gst_amount"] = (df["total_price"] * df["gst%"]) / 100

    # Group data by year-month and calculate summary statistics
    summary_df = df.groupby("year-month").agg({
        "quantity": "sum",
        "total_price": "sum",
        "discount": "sum",
        "gst_amount": "sum"
    }).reset_index()

    return summary_df

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
    invoice_number=st.text_input("Invoice Number")
    today = datetime.now()
    invoice_date=today.strftime("%m/%d/%y")
    store_name=st.text_input("Store Name")
    product_name = st.text_input("Product Name")
    unit_price = st.number_input("Unit Price", min_value=0.0)
    quantity = st.number_input("Quantity", min_value=0.0)
    total_price = st.number_input("Total Price", min_value=0.0)
    discount = st.number_input("Discount", min_value=0.0)
    gst = st.number_input("GST%", min_value=0.0)

    if st.button("Add Product"):
        if st.session_state.logged_in:
            product = {
                "username": st.session_state.username,
                "invoice_number": invoice_number,
                "invoice_date": invoice_date,
                "store_name":store_name,
                "product_name": product_name,
                "unit_price": unit_price,
                "quantity": quantity,
                "total_price": total_price,
                "discount": discount,
                "gst%": gst,
            }
            product_collection.insert_one(product)
            st.success("Product added successfully!")
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
            summary_df = generate_summary_from_mongodb(st.session_state.username)

            if summary_df is not None:
                # Prepare the output buffer
                output_buffer = BytesIO()

                # Use ExcelWriter to write multiple sheets
                with pd.ExcelWriter(output_buffer, engine="openpyxl") as writer:
                    # Save detailed data to a separate sheet
                    product_data = list(product_collection.find({"username": st.session_state.username}))
                    if product_data:
                        product_df = pd.DataFrame(product_data)
                        # Drop MongoDB-specific fields for cleaner output if needed
                        product_df.to_excel(writer, sheet_name="Product Details", index=False)

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

