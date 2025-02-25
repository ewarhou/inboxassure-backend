import requests
from collections import defaultdict
from pprint import pprint
from datetime import datetime

def check_bison_duplicates():
    # CoachPro Bison API credentials
    base_url = "https://app.orbitmailboost.com"
    api_key = "31|RjyhS0du3NfBBmSljYkS0t3rJEA1F8klIkFNiSud74ed1d0a"

    all_accounts = []
    current_page = 1
    per_page = 15  # Set to 15 since this seems to be the API limit
    page_stats = []

    print("\n🔍 Fetching accounts from Bison API...")
    
    while True:
        response = requests.get(
            f"{base_url}/api/sender-emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            params={
                "page": current_page,
                "per_page": per_page
            }
        )
        
        if response.status_code != 200:
            print(f"❌ API Error: {response.text}")
            break
            
        data = response.json()
        accounts = data.get('data', [])
        
        # Store page statistics
        page_stats.append({
            'page': current_page,
            'accounts_count': len(accounts),
            'emails': [acc.get('email') for acc in accounts if acc.get('email')],
            'first_id': accounts[0].get('id') if accounts else None,
            'last_id': accounts[-1].get('id') if accounts else None
        })
        
        all_accounts.extend(accounts)
        
        # Check if we've reached the last page
        total_pages = data.get('meta', {}).get('last_page', 1)
        if current_page >= total_pages:
            break
            
        current_page += 1

    print(f"\n✅ Total accounts fetched: {len(all_accounts)}")

    # Group accounts by email
    email_groups = defaultdict(list)
    for account in all_accounts:
        email = account.get('email')
        if email:
            email_groups[email].append({
                'id': account.get('id'),
                'email': email,
                'status': account.get('status'),
                'daily_limit': account.get('daily_limit'),
                'created_at': account.get('created_at'),
                'updated_at': account.get('updated_at'),
                'tags': [tag.get('name') for tag in account.get('tags', [])],
                'page_found': next(
                    (stats['page'] for stats in page_stats 
                     if account.get('id') in [stats['first_id'], stats['last_id']] 
                     or account.get('email') in stats['emails']),
                    'unknown'
                )
            })

    # Find and analyze duplicates
    duplicates = {email: accounts for email, accounts in email_groups.items() if len(accounts) > 1}

    if duplicates:
        print("\n⚠️ Found duplicate emails:")
        print(f"Total emails with duplicates: {len(duplicates)}")
        
        # Sort duplicates by number of instances (most duplicates first)
        sorted_duplicates = sorted(duplicates.items(), key=lambda x: len(x[1]), reverse=True)
        
        for email, accounts in sorted_duplicates:
            print(f"\n{'='*80}")
            print(f"📧 {email}")
            print(f"Found {len(accounts)} instances:")
            
            # Sort accounts by ID
            sorted_accounts = sorted(accounts, key=lambda x: x['id'])
            
            for idx, account in enumerate(sorted_accounts, 1):
                print(f"\n   Instance {idx}:")
                print(f"   ID: {account['id']}")
                print(f"   Status: {account['status']}")
                print(f"   Daily Limit: {account['daily_limit']}")
                print(f"   Created: {account.get('created_at', 'N/A')}")
                print(f"   Updated: {account.get('updated_at', 'N/A')}")
                print(f"   Found on Page: {account['page_found']}")
                if account['tags']:
                    print(f"   Tags: {', '.join(account['tags'])}")

        # Print summary statistics
        print(f"\n{'='*80}")
        print("\n📊 Duplicate Summary:")
        duplicate_counts = defaultdict(int)
        for email, accounts in duplicates.items():
            duplicate_counts[len(accounts)] += 1
        
        for count, freq in sorted(duplicate_counts.items()):
            print(f"   {freq} email{'s' if freq > 1 else ''} found {count} times")

    else:
        print("\n✅ No duplicate emails found")

    # Print overall statistics
    print(f"\n📊 Overall Statistics:")
    print(f"   - Total accounts: {len(all_accounts)}")
    print(f"   - Unique emails: {len(email_groups)}")
    print(f"   - Duplicate entries: {sum(len(accounts) - 1 for accounts in email_groups.values())}")
    print(f"   - Pages fetched: {len(page_stats)}")
    print(f"   - Average accounts per page: {len(all_accounts) / len(page_stats):.1f}")

if __name__ == "__main__":
    check_bison_duplicates()