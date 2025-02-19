import requests
from django.conf import settings
from settings.api import log_to_terminal

class InstantlyService:
    def __init__(self, user):
        print(f"\n[InstantlyService] Initializing for user: {user.email}")
        self.user = user
        self.base_url = "https://app.instantly.ai/backend-alt/api/v1"
        
        # Get all organizations
        self.organizations = []
        try:
            orgs = user.instantly_organizations.all()
            print(f"[InstantlyService] Found {orgs.count()} organizations")
            
            for org in orgs:
                org_data = {
                    'id': org.id,
                    'name': org.instantly_organization_name,
                    'org_id': org.instantly_organization_id,
                    'org_token': org.instantly_organization_token,
                    'status': org.instantly_organization_status
                }
                
                if hasattr(org.user, 'settings'):
                    org_data['user_token'] = org.user.settings.instantly_user_token
                else:
                    org_data['user_token'] = None
                
                self.organizations.append(org_data)
                print(f"\n[InstantlyService] Organization details:")
                print(f"  Name: {org_data['name']}")
                print(f"  ID: {org_data['org_id']}")
                print(f"  Status: {org_data['status']}")
                print(f"  User token: {'Present' if org_data['user_token'] else 'Missing'}")
                print(f"  Org token: {'Present' if org_data['org_token'] else 'Missing'}")
                
        except Exception as e:
            print(f"[InstantlyService] Init Error - Failed to get organizations: {str(e)}")
            print(f"[InstantlyService] Error details:", e.__class__.__name__)
            self.organizations = []
        
        log_to_terminal("InstantlyService", "Init", f"Initialized with {len(self.organizations)} organizations")

    def get_tags(self):
        """Get all tags from all Instantly organizations"""
        print("\n[InstantlyService] Starting get_tags request")
        
        if not self.organizations:
            print("[InstantlyService] Error - No organizations found")
            log_to_terminal("InstantlyService", "Error", "No organizations found")
            return []
        
        all_tags = []
        seen_tags = set()  # To track unique tags
        
        for org in self.organizations:
            print(f"\n[InstantlyService] Getting tags for organization: {org['name']}")
            
            if not org['org_token'] or not org['user_token']:
                print(f"[InstantlyService] Skipping org - Missing tokens:")
                print(f"  Organization token: {'Present' if org['org_token'] else 'Missing'}")
                print(f"  User token: {'Present' if org['user_token'] else 'Missing'}")
                continue
            
            headers = {
                "X-Org-Auth": org['org_token'],
                "Cookie": f"__session={org['user_token']}"
            }
            endpoint = f"{self.base_url}/custom-tag?limit=100"
            print(f"[InstantlyService] Request details:")
            print(f"  Endpoint: {endpoint}")
            print(f"  Headers: {headers}")
            
            try:
                print("[InstantlyService] Sending request...")
                response = requests.get(endpoint, headers=headers)
                print(f"[InstantlyService] Response received:")
                print(f"  Status: {response.status_code}")
                print(f"  Headers: {dict(response.headers)}")
                
                if response.status_code == 200:
                    response_data = response.json()
                    print("\n[InstantlyService] Full response data:")
                    print(response_data)
                    
                    tags = response_data.get('data', [])
                    print(f"\n[InstantlyService] Success - Retrieved {len(tags)} tags")
                    print(f"[InstantlyService] Response metadata:")
                    print(f"  Total: {response_data.get('total')}")
                    print(f"  Limit: {response_data.get('limit')}")
                    print(f"  Skip: {response_data.get('skip')}")
                    
                    # Add new unique tags
                    for tag in tags:
                        tag_id = tag['id']
                        if tag_id not in seen_tags:
                            seen_tags.add(tag_id)
                            all_tags.append({
                                'id': tag_id,
                                'name': tag['label'],
                                'organization': org['name']
                            })
                            print(f"[InstantlyService] Added tag: {tag['label']} from {org['name']}")
                else:
                    print(f"[InstantlyService] Error - Failed with status {response.status_code}")
                    print(f"[InstantlyService] Error response: {response.text}")
            except Exception as e:
                print(f"[InstantlyService] Error - Request failed for org {org['name']}: {str(e)}")
                print(f"[InstantlyService] Error details:", e.__class__.__name__)
        
        print(f"\n[InstantlyService] Retrieved total of {len(all_tags)} unique tags from {len(self.organizations)} organizations")
        return all_tags 