import requests
import json
import mysql.connector
from datetime import datetime

def get_credentials():
    try:
        # Connect to MySQL
        conn = mysql.connector.connect(
            host="64.227.20.217",
            user="amine",
            password="Warhou19981@",
            database="inboxassure"
        )
        cursor = conn.cursor(dictionary=True)
        
        # Get credentials for Dynamic ESP Routing (LT)
        cursor.execute("""
            SELECT 
                ui.instantly_organization_id as org_id,
                ui.instantly_organization_name as org_name,
                ui.instantly_organization_token as org_token,
                us.instantly_user_token as user_token,
                us.instantly_user_id as user_id
            FROM user_instantly ui 
            JOIN user_settings us ON ui.user_id = us.user_id 
            WHERE ui.instantly_organization_name = 'Dynamic ESP Routing (LT)'
            LIMIT 1
        """)
        
        credentials = cursor.fetchone()
        if credentials:
            # Format the session cookie with the user token
            credentials['token'] = f'__session={credentials["user_token"]}'
            print(f"\nOrganization Details:")
            print(f"Name: {credentials['org_name']}")
            print(f"ID: {credentials['org_id']}")
            print(f"User ID: {credentials['user_id']}\n")
        else:
            print("No credentials found for Dynamic ESP Routing (LT)")
        
        cursor.close()
        conn.close()
        
        return credentials
    except Exception as e:
        print(f"Error getting credentials: {e}")
        return None

def test_campaign_data():
    credentials = get_credentials()
    if not credentials:
        print("No credentials found")
        return
    
    # Use the specific campaign ID
    campaign_id = "3bfdd2a0-65c4-48c9-90e6-c87a8aa25a50"
    print(f"Using campaign ID: {campaign_id}")
    
    # Headers for the request
    headers = {
        'Cookie': credentials['token'],
        'X-Org-Auth': credentials['org_token'],
        'X-Org-Id': credentials['org_id'],
        'Content-Type': 'application/json'
    }
    
    # Test campaign data endpoint
    print("\nTesting campaign data endpoint:")
    url = "https://app.instantly.ai/api/campaign/get_campaign_data"
    data = {
        "campaignID": campaign_id
    }
    
    print(f"Headers: {headers}")
    print(f"Data: {data}\n")
    
    response = requests.post(url, headers=headers, json=data)
    print(f"Response Status: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    print(f"Response Body: {response.text}")

def test_update_campaign_options():
    credentials = get_credentials()
    if not credentials:
        print("No credentials found")
        return
    
    # Headers for the request
    headers = {
        'Cookie': credentials['token'],
        'X-Org-Auth': credentials['org_token'],
        'X-Org-Id': credentials['org_id'],
        'Content-Type': 'application/json'
    }
    
    test_cases = [
        {
            "name": "Valid request",
            "data": {
                "campaignID": "3bfdd2a0-65c4-48c9-90e6-c87a8aa25a50",
                "orgID": credentials['org_id'],
                "emailList": ["test@example.com"],
                "openTracking": True,
                "linkTracking": True,
                "stopOnReply": True,
                "stopOnAutoReply": True,
                "textOnly": False,
                "dailyLimit": 100,
                "emailGap": 30
            }
        },
        {
            "name": "Invalid campaign ID",
            "data": {
                "campaignID": "invalid-uuid",
                "orgID": credentials['org_id'],
                "emailList": ["test@example.com"],
                "openTracking": True
            }
        },
        {
            "name": "Invalid organization ID",
            "data": {
                "campaignID": "3bfdd2a0-65c4-48c9-90e6-c87a8aa25a50",
                "orgID": "invalid-org-id",
                "emailList": ["test@example.com"],
                "openTracking": True
            }
        },
        {
            "name": "Missing required fields",
            "data": {
                "emailList": ["test@example.com"],
                "openTracking": True
            }
        },
        {
            "name": "Invalid email format",
            "data": {
                "campaignID": "3bfdd2a0-65c4-48c9-90e6-c87a8aa25a50",
                "orgID": credentials['org_id'],
                "emailList": ["invalid-email"],
                "openTracking": True
            }
        },
        {
            "name": "Invalid daily limit",
            "data": {
                "campaignID": "3bfdd2a0-65c4-48c9-90e6-c87a8aa25a50",
                "orgID": credentials['org_id'],
                "dailyLimit": -1
            }
        },
        {
            "name": "Invalid tag ID format",
            "data": {
                "campaignID": "3bfdd2a0-65c4-48c9-90e6-c87a8aa25a50",
                "orgID": credentials['org_id'],
                "emailTagList": ["invalid-tag-id"]
            }
        }
    ]
    
    # Test update campaign options endpoint
    url = "https://app.instantly.ai/api/campaign/update/options"
    
    for test_case in test_cases:
        print(f"\n\nTesting {test_case['name']}:")
        print(f"Headers: {headers}")
        print(f"Data: {test_case['data']}\n")
        
        response = requests.post(url, headers=headers, json=test_case['data'])
        print(f"Response Status: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        print(f"Response Body: {response.text}")
        print("-" * 80)

def test_update_sequences():
    credentials = get_credentials()
    if not credentials:
        print("No credentials found")
        return
    
    # Use the specific campaign ID
    campaign_id = "3bfdd2a0-65c4-48c9-90e6-c87a8aa25a50"
    print(f"Using campaign ID: {campaign_id}")
    
    # Headers for the request
    headers = {
        'Cookie': credentials['token'],
        'X-Org-Auth': credentials['org_token'],
        'X-Org-Id': credentials['org_id'],
        'Content-Type': 'application/json'
    }
    
    data = {
        "sequences": [{
            "steps": [
                {
                    "type": "email",
                    "variants": [
                        {
                            "subject": "From Cursor - First Email A",
                            "body": "<div>Hello&nbsp;from&nbsp;cursor&nbsp;-&nbsp;First&nbsp;variant&nbsp;A</div>"
                        },
                        {
                            "subject": "From Cursor - First Email B",
                            "body": "<div>Hello&nbsp;from&nbsp;cursor&nbsp;-&nbsp;First&nbsp;variant&nbsp;B</div>"
                        }
                    ]
                },
                {
                    "type": "email",
                    "variants": [
                        {
                            "subject": "From Cursor - Follow Up",
                            "body": "<div>This&nbsp;is&nbsp;a&nbsp;follow-up&nbsp;from&nbsp;cursor</div>"
                        }
                    ]
                },
                {
                    "type": "email",
                    "variants": [
                        {
                            "subject": "From Cursor - Final Email A",
                            "body": "<div>Final&nbsp;message&nbsp;from&nbsp;cursor&nbsp;-&nbsp;Variant&nbsp;A</div>"
                        },
                        {
                            "subject": "From Cursor - Final Email B",
                            "body": "<div>Final&nbsp;message&nbsp;from&nbsp;cursor&nbsp;-&nbsp;Variant&nbsp;B</div>"
                        }
                    ]
                }
            ]
        }],
        "campaignID": campaign_id,
        "orgID": credentials['org_id']
    }
    
    # Test update sequences endpoint
    url = "https://app.instantly.ai/api/campaign/update/sequences"
    
    print("\nTesting sequence update:")
    print(f"Headers: {headers}")
    print(f"Data: {data}\n")
    
    response = requests.post(url, headers=headers, json=data)
    print(f"Response Status: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    print(f"Response Body: {response.text}")
    print("-" * 80)

def test_list_accounts():
    credentials = get_credentials()
    if not credentials:
        print("No credentials found")
        return
    
    print("\nTesting List Accounts endpoint:")
    
    # Headers for the request
    headers = {
        'Cookie': credentials['token'],
        'X-Org-Auth': credentials['org_token'],
        'X-Org-Id': credentials['org_id'],
        'Content-Type': 'application/json'
    }
    
    # Test cases with different parameters
    test_cases = [
        {
            "name": "Basic list with default parameters",
            "data": {
                "limit": 10,
                "skip": 0
            }
        },
        {
            "name": "List with search query",
            "data": {
                "search": "test",
                "limit": 10,
                "skip": 0
            }
        },
        {
            "name": "List with tags included",
            "data": {
                "limit": 10,
                "skip": 0,
                "include_tags": True
            }
        },
        {
            "name": "List with filter",
            "data": {
                "limit": 10,
                "skip": 0,
                "filter": {
                    "status": "active"
                }
            }
        }
    ]
    
    # Test list accounts endpoint
    url = "https://app.instantly.ai/backend/api/v1/account/list"
    
    for test_case in test_cases:
        print(f"\nTesting {test_case['name']}:")
        print(f"Headers: {headers}")
        print(f"Data: {test_case['data']}\n")
        
        response = requests.post(url, headers=headers, json=test_case['data'])
        print(f"Response Status: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        print(f"Response Body: {response.text}")
        print("-" * 80)

def test_update_account():
    credentials = get_credentials()
    if not credentials:
        print("No credentials found")
        return
    
    print("\nTesting Update Account endpoint:")
    
    # Headers for the request
    headers = {
        'Cookie': credentials['token'],
        'X-Org-Auth': credentials['org_token'],
        'X-Org-Id': credentials['org_id'],
        'Content-Type': 'application/json'
    }
    
    # Test cases
    test_cases = [
        {
            "name": "Valid update",
            "data": {
                "email": "a.addertongue@dosohq.com",
                "daily_limit": "1000",
                "sending_gap": "0",
                "warmup_limit": "25"
            }
        },
        {
            "name": "Invalid email",
            "data": {
                "email": "nonexistent@dosohq.com",
                "daily_limit": "1000"
            }
        },
        {
            "name": "Missing email",
            "data": {
                "daily_limit": "1000",
                "sending_gap": "0"
            }
        },
        {
            "name": "Invalid daily limit format",
            "data": {
                "email": "a.addertongue@dosohq.com",
                "daily_limit": -1000
            }
        },
        {
            "name": "Invalid warmup limit format",
            "data": {
                "email": "a.addertongue@dosohq.com",
                "warmup_limit": "invalid"
            }
        }
    ]
    
    # Test update account endpoint
    url = "https://app.instantly.ai/backend/api/v1/account/update"
    
    for test_case in test_cases:
        print(f"\nTesting {test_case['name']}:")
        print(f"Headers: {headers}")
        print(f"Data: {test_case['data']}\n")
        
        response = requests.post(url, headers=headers, json=test_case['data'])
        print(f"Response Status: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        print(f"Response Body: {response.text}")
        print("-" * 80)

if __name__ == "__main__":
    # test_campaign_data()
    # test_update_campaign_options()
    # test_update_sequences()
    # test_list_accounts()
    test_update_account() 