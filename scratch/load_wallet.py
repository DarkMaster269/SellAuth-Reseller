import os

import requests

RPC_URL = "http://127.0.0.1:7777"
AUTH = ("electrum", "DARK2695@")
PASSWORD = "DARK2695@"

# Let's find the exact file name
wallet_name = "sellauth_resell_wallet"
wallet_dir = r"C:\Users\dark2\AppData\Roaming\Electrum-LTC\wallets"

path_sqlite = os.path.join(wallet_dir, wallet_name + ".sqlite")
path_no_ext = os.path.join(wallet_dir, wallet_name)

wallet_path = None
if os.path.exists(path_sqlite):
    wallet_path = path_sqlite
    print(f"✅ Found wallet file: {wallet_path}")
elif os.path.exists(path_no_ext):
    wallet_path = path_no_ext
    print(f"✅ Found wallet file: {wallet_path}")
else:
    print("❌ Could not find wallet file. Please check the exact name in your wallet folder.")
    exit(1)

print("Sending load_wallet command to the GUI server...")
payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "load_wallet",
    "params": {"wallet_path": wallet_path, "password": PASSWORD},
}

res = requests.post(RPC_URL, json=payload, auth=AUTH)
print("Load Response:", res.text)

print("\nChecking list_wallets to verify...")
list_payload = {"jsonrpc": "2.0", "id": 2, "method": "list_wallets"}
list_res = requests.post(RPC_URL, json=list_payload, auth=AUTH)
print("List Response:", list_res.text)
