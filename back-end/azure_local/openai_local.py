import json
import asyncio
import ast
import semantic_kernel as sk
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from azure_local.search_service import search_index

asd_mappings = {
    "Introduction": ["Operational Excellence"],
    "Key Functionalities/Capabilities": ["Portability and Modularization"],
    "Assumptions/Constraints/Recommendations": ["Reliability"],
    "User/Usage Requirements": ["Support"],
    "Interface Requirements": ["Security and Governance"],
    "Security Requirements": ["Security and Governance"],
    "Network Requirements": ["Operational Excellence"],
    "Software Requirements": ["Support"],
    "Performance Requirements": ["Performance and Efficiency"],
    "Supportability Requirements": ["Support"],
    "Storage Requirements": ["Cost Optimization", "Portability and Modularization"],
    "Database Requirements": ["Portability and Modularization"],
    "Disaster Recovery Requirements": ["Reliability"],
    "Compliance Requirements": ["Security and Governance"],
    "Licensing Requirements": ["Cost Optimization"],
    "Proposed Solution": ["Operational Excellence", "Reliability"],
    "EC2 Sizing/Specifications": ["Cost Optimization"],
    "On-Prem Servers Sizing/Specification": ["Cost Optimization"],
    "Deployment Details": ["Security and Governance"]
}

iac_sections = ["Introduction", "Assumptions/Constraints/Recommendations", "Interface Requirements", "Network Requirements", "Software Requirements", "Storage Requirements", "Database Requirements", "EC2 Sizing/Specifications"]

def setup():
    kernel = sk.Kernel()

    deployment, api_key, endpoint = sk.azure_openai_settings_from_dot_env()

    kernel.add_service(
        AzureChatCompletion(
            service_id="dv",
            deployment_name=deployment,
            base_url=endpoint + 'openai',
            api_key=api_key
        )
    )

    return kernel

async def generate_iac(asd: dict):
    kernel = setup()

    req_settings = kernel.get_prompt_execution_settings_from_service_id("dv")
    req_settings.max_tokens = 2000
    req_settings.temperature = 0
    req_settings.top_p = 0.95

    content = ""

    for section in iac_sections:
        asd_content = asd[section]

        if isinstance(asd_content, list):
            for item in asd_content:
                content += json.dumps(item)
        else:
            if asd_content and "N/A" not in asd_content:
                content += asd_content

    prompt = [
        {
            "role": "system",
            "content": """As an AI assistant, your task is to generate basic starter Terraform scripts for AWS for any possible components listed within the content 
                          of an architecture design document. Clearly indicate the component with a comment at the start of each component. 
                          The relevant content will be under Content Section. Return the scripts as a list of strings. Each 
                          script should be a seperate entry in this list.
                        """ 
        },
        {
            "role": "user",
            "content": f"""
                        [Content Section]
                        {content}
                        [Content Section]
                       """
        },
    ]

    prompt_template_config = sk.PromptTemplateConfig(
        template=json.dumps(prompt),
        name="generate_iac",
        template_format="semantic-kernel",
        execution_settings=req_settings,
    )

    function = kernel.create_function_from_prompt(
        function_name="generate_iac",
        plugin_name="generate_iac",
        prompt_template_config=prompt_template_config,
    )

    result = await kernel.invoke(function)
    print(result)

    result_str = str(result.value[0].content)
    print(result_str)

    result_list = ast.literal_eval(result_str)
    
    return result_list


async def validate_asd(asd: dict):
    kernel = setup()

    req_settings = kernel.get_prompt_execution_settings_from_service_id("dv")
    req_settings.max_tokens = 2000
    req_settings.temperature = 0
    req_settings.top_p = 0.95
    
    tasks = []
    for section, content in asd.items():
        if content and "N/A" not in content:
            policy_categories = asd_mappings[section]
            for category in policy_categories:
                task = validate_section(kernel, req_settings, category, content)
                tasks.append(task)

    results = await asyncio.gather(*tasks)

    return results


async def validate_section(kernel, req_settings, policy_category, asd_content):
    policies = search_index(policy_category)

    prompt = [
        {
            "role": "system",
            "content": """As an AI assistant, your task is to validate a given section from an architecture design document against
                          specific security and cloud policies. The section will be under Design Document Section, and the policies will be under Policies. 
                          Identify any violations or deviations in the section based on the policies. When you reference a design document section, refer to it by name.
                          If you receive an empty section, ignore it and do not return anything regarding it at all. Do not make return violations or deviations like 'this section is empty and it should contain details.'
                          When referring to policies in your response, replace all underscores with spaces. For example, if referencing 'Security_by_design', replace with just 'Security by design.'
                          Return the result in a JSON object with the following schema:
                          {"Type": "Violation or Deviation","Issue": "brief issue title","Description": "description goes here","Principles": "insert header property of the policy section here with all underlines replaced with spaces","Mandatory": true}
                          For the mandatory field of the result JSON object, it should depend on the mandatory field of the policy object. If mandatory on the policy is true, then the mandatory field of the result should be true. Otherwise, it is false.
                          Please just return the valid JSON object itself without the string quotes and no newlines. Do not return a list of JSON objects or any other format, just one JSON object.
                        """ 
        },
        {
            "role": "user",
            "content": f"""
                        [Design Document Section]
                        {asd_content}
                        [Design Document Section]

                        [Policies]
                        {policies}
                        [Policies]
                       """
        },
    ]

    prompt_template_config = sk.PromptTemplateConfig(
        template=json.dumps(prompt),
        name="validate_section",
        template_format="semantic-kernel",
        execution_settings=req_settings,
    )

    function = kernel.create_function_from_prompt(
        function_name="validate_section",
        plugin_name="validate_section",
        prompt_template_config=prompt_template_config,
    )

    result = await kernel.invoke(function)

    result_json = json.loads(result.value[0].content)

    print(result_json)
    print("\n\n")
    
    return result_json
