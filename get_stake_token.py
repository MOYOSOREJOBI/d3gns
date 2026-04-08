"""
Helper to get your Stake API token via email/password login.
Run once to print your token, then paste it into config.py.

Usage:
    python get_stake_token.py
"""

import requests
import getpass

LOGIN_MUTATION = """
mutation Login($email: String!, $password: String!, $tfaToken: String) {
    login(email: $email password: $password tfaToken: $tfaToken) {
        token
        user {
            id
            name
            email
        }
    }
}
"""

def get_token():
    email    = input("Stake email: ")
    password = getpass.getpass("Stake password: ")
    tfa      = input("2FA code (press Enter if none): ").strip() or None

    resp = requests.post(
        "https://stake.com/_api/graphql",
        json={
            "query"    : LOGIN_MUTATION,
            "variables": {
                "email"   : email,
                "password": password,
                "tfaToken": tfa,
            },
        },
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    data = resp.json()

    if "errors" in data:
        print(f"\nLogin failed: {data['errors']}")
        return

    token = data["data"]["login"]["token"]
    user  = data["data"]["login"]["user"]
    print(f"\nLogged in as: {user['name']} ({user['email']})")
    print(f"\nYour token (copy this into config.py → STAKE_API_TOKEN):\n")
    print(f"  {token}\n")


if __name__ == "__main__":
    get_token()
