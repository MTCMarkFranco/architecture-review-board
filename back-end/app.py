import asyncio
from flask import Flask, request
from flask_cors import CORS
from azure_local.search_service import create_policy_index, upload_policies, search_index
from azure_local.openai_local import validate_asd, generate_iac
from file_processing.parsing import parse_asd


app = Flask(__name__)
CORS(app)

@app.route("/validateasd", methods=["POST"])
def validate():
    if 'file' not in request.files:
        return 'No file part', 400
    
    file = request.files['file']

    if file.filename == '':
        return 'No selected file', 400
    
    if file:
        print(f"Received file: {file.filename}")

    asd = parse_asd(pdf_file=file)

    result = asyncio.run(validate_asd(asd))

    return result

@app.route("/geniac", methods=["POST"])
def geniac():
    if 'file' not in request.files:
        return 'No file part', 400
    
    file = request.files['file']

    if file.filename == '':
        return 'No selected file', 400
    
    if file:
        print(f"Received file: {file.filename}")

    asd = parse_asd(pdf_file=file)

    result = asyncio.run(generate_iac(asd))

    return result


if __name__ == '__main__':
    index = 'policy_index'
    policies_path = './file_processing/data/policies.json'

    # create_policy_index(index)
    # upload_policies(index, policies_path)

    app.run(debug=True)
