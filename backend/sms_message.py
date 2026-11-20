from twilio.rest import Client
import os

# Replace these with your Twilio credentials
account_sid = os.getenv("ACCOUNT_SID")
auth_token = os.getenv("AUTH_TOKEN_TWILIO") 
twilio_number = os.getenv("TWILIO_NUM")
destination_number = os.getenv("RECEIVING_NUM")

client = Client(account_sid, auth_token)

message = client.messages.create(
    body='stop-stopwatch',
    from_=twilio_number,
    to=destination_number
)

print(f"Message sent! SID: {message.sid}")
