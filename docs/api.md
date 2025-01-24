### Update Campaign Sequences
**POST** `/api/campaign/update/sequences`

Updates the sequence steps for a campaign. Supports multiple steps with multiple variants for A/B testing.

**Important Notes**:
- The API is sensitive to the exact structure of the request body
- HTML content must use `&nbsp;` for spaces (e.g., `<div>Hello&nbsp;from&nbsp;cursor</div>`)
- Do not include extra fields not specified in the structure below
- Maintain consistent JSON formatting

**Request Headers**
```json
{
  "Cookie": "string",
  "X-Org-Auth": "string",
  "X-Org-Id": "string",
  "Content-Type": "application/json"
}
```

**Request Body Structure**
```json
{
  "sequences": [{
    "steps": [{
      "type": "email",
      "variants": [{
        "subject": "string",
        "body": "string (HTML content with &nbsp; for spaces)"
      }]
    }]
  }],
  "campaignID": "string (UUID)",
  "orgID": "string (UUID)"
}
```

**Example Request Body**
```json
{
  "sequences": [{
    "steps": [
      {
        "type": "email",
        "variants": [
          {
            "subject": "First Email A",
            "body": "<div>Hello&nbsp;-&nbsp;First&nbsp;variant&nbsp;A</div>"
          },
          {
            "subject": "First Email B",
            "body": "<div>Hello&nbsp;-&nbsp;First&nbsp;variant&nbsp;B</div>"
          }
        ]
      },
      {
        "type": "email",
        "variants": [
          {
            "subject": "Follow Up Email",
            "body": "<div>This&nbsp;is&nbsp;a&nbsp;follow-up</div>"
          }
        ]
      }
    ]
  }],
  "campaignID": "3bfdd2a0-65c4-48c9-90e6-c87a8aa25a50",
  "orgID": "460aac1e-e8e5-4431-b449-caa7c5ee1a6d"
}
```

**Success Response (200)**
```json
{
  "status": "success"
}
```

**Error Responses**

*Invalid Campaign ID or Organization ID (500)*
```json
{
  "statusCode": 500,
  "code": "22P02",
  "error": "Internal Server Error",
  "message": "invalid input syntax for type uuid: \"invalid-uuid\""
}
```

*Empty Sequences Array (200)*
```json
{
  "error": "Error I503 - please contact support"
}
```

**Notes**
- Each step can have multiple variants for A/B testing
- Email body must use HTML content with proper space encoding (`&nbsp;`)
- The structure must be followed exactly as shown
- Empty sequences array will result in an error 