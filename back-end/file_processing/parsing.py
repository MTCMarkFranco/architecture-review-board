import fitz
import json
 
summary_headers = [
    "Introduction",
    "Key Functionalities/Capabilities",
    "Assumptions/Constraints/Recommendations"
]

requirement_headers = [
    "User/Usage Requirements", 
    "Interface Requirements",
    "Security Requirements",
    "Network Requirements",
    "Software Requirements",
    "Performance Requirements",
    "Supportability Requirements",
    "Storage Requirements",
    "Database Requirements",
    "Disaster Recovery Requirements",
    "Compliance Requirements",
    "Licensing Requirements"
]

solution_headers = [
    "Proposed New Architecture",
    "Pre-Production Architecture",
    "Production/DR Architecture"
]

ec2_table_headers = [
    "Environment",
    "Account Type",
    "Network Zone",
    "AWS Region",
    "AZ",
    "OS",
    "Instance Type CPU/RAM",
    "Count",
    "Storage Type",
    "Storage Volume Size",
    "Domain/DNS",
    "Data Residency Restrictions",
    "Data Classification",
    "Server Role",
    "On/Off Scheduling"
]

servers_table_headers = [
    "Environment/Location",
    "Server Type",
    "OS",
    "Network Zone",
    "CPU Cores",
    "RAM",
    "Non-OS SAN Storage",
    "Count",
    "Domain/DNS",
    "Data Residency Restrictions",
    "Data Classification",
    "Server Role"
]

deployment_details_headers = [
    "Hosted Location",
    "Countries/Regions Serviced",
    "Business Unit(s)"
]

def parse_asd(pdf_path='', pdf_file=None, local=False):
    if local:
        asd = fitz.open(pdf_path)
    else:
        file_stream = pdf_file.read()
        asd = fitz.open(stream=file_stream, filetype='pdf')

    section_content = {}

    summary = extract_section(asd, "Summary", "Solution Requirements", summary_headers)
    section_content.update(summary)

    requirements = extract_section(asd, "Solution Requirements", "Affinity/Anti-Affinity Requirements", requirement_headers)
    section_content.update(requirements)

    proposed_solution = extract_section(asd, "Proposed Solution", "EC2 Sizing/Specifications (Guidance on OS Volumes & MS Office Support)", solution_headers)
    prune(proposed_solution)
    section_content["Proposed Solution"] = proposed_solution

    ec2s = extract_table(asd, "EC2 Sizing/Specifications", "On-Prem Servers Sizing/Specifications", ec2_table_headers)
    section_content["EC2 Sizing/Specifications"] = ec2s

    servers = extract_table(asd, "On-Prem Servers Sizing/Specifications", "Proposed Server Details", servers_table_headers)
    section_content["On-Prem Servers Sizing/Specification"] = servers

    deployment_details = extract_table(asd, "Hosted Location", "Miscellaneous Information", deployment_details_headers, False)
    section_content["Deployment Details"] = deployment_details

    output_path = "./file_processing/data/asd.json"

    with open(output_path, 'w') as jsonfile:
        json.dump(section_content, jsonfile, indent=4)

    asd.close()

    return section_content



def extract_section(doc, section_header, ending_header, subheaders):
    sections = {}
    at_section = False
    cur_subsection = ''
    header_count = 0

    for page_number in range(len(doc)):
        page = doc.load_page(page_number)
        page_text = page.get_text("text")

        for line in page_text.splitlines():
            line = line.strip()
            if line == section_header:
                header_count += 1

                if header_count == 2:
                    at_section = True
                    continue
            
            if at_section and line == ending_header:
                break

            if at_section:
                if line in subheaders:
                    cur_subsection = line
                    sections[cur_subsection] = ''
                else:
                    if cur_subsection != '':
                        sections[cur_subsection] = sections[cur_subsection] + line + " "
        else:
            continue
        break

    return sections


def extract_table(doc, table_header, ending_header, table_headers, section_header=True):
    desired_table = []
    at_table = False
    entries = []

    for page_number in range(len(doc)):
        page = doc.load_page(page_number)

        if not at_table:
            tabs = page.find_tables()
            for table in tabs.tables:
                cur_table = table.extract()

                if table_header in cur_table[0][0]:
                    for line in cur_table:
                        desired_table.append(line)

                    at_table = True
                else:
                    continue
        else:
            tabs = page.find_tables()
            for table in tabs.tables:
                cur_table = table.extract()

                if ending_header in cur_table[0][0]:
                    break

                for line in cur_table:
                    desired_table.append(line)
            else:
                continue
            break
    
    
    start_index = 2 if section_header else 1

    for row in desired_table[start_index:]:
        if row[0] == '':
            break
            
        entry = {}

        for index, item in enumerate(row):
            entry[table_headers[index]] = item.replace('\n', ' ')

        entries.append(entry)

    return entries


def prune(section):
    if not isinstance(section, dict):
        return

    empty = [key for key, value in section.items() if not value]
    for key in empty:
        del section[key]


def extract_policies(pdf_path):
    pdf_document = fitz.open(pdf_path)

    sections = {}
    cur_line = ''
 
    for page_number in range(len(pdf_document)):
        page = pdf_document[page_number]
 
        page_text = page.get_text("text")

        for line in page_text.splitlines():
            if line.isupper() and 'INTERNAL' not in line and line.startswith(('0', '1', '2', '3', '4', '5', '6', '7', '8', '9')):
                line = line.strip()
                cur_line = line
                sections[cur_line] = ''
            else:
                if cur_line != '':
                    sections[cur_line] = sections[cur_line] + line.strip()

    pdf_document.close()
 
    json_array = [{"header": key, "content": value} for key, value in sections.items()]

    return json_array
 