import requests

url = "https://betacloud.ncs.gov.in/api/v1/job-posts/search"
params = {"page": 0, "size": 20}
headers = {"Content-Type": "application/json"}
body = {"sortBy": "RELEVANCE", "userId": "", "pwdCandidateWelcome": True}

print("Making request...")
try:
    response = requests.post(url, params=params, headers=headers, json=body)
    print(f"Status Code: {response.status_code}")
    print(f"Headers: {response.headers}")
    print(f"Text snippet: {response.text[:500]}")
except Exception as e:
    print(f"Error: {e}")
