import os
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")

client = WebClient(token=SLACK_BOT_TOKEN)


def post_plan(channel_id, incident_text, plan_json):
    """
    Posts an incident plan to Slack.
    plan_json is expected to have 'plan', 'risks', 'confidence', 'sources'.
    """
    steps = "\n".join(f"• {s}" for s in plan_json.get("steps", []))
    risks = "\n".join(f"• {r}" for r in plan_json.get("risks", []))
    confidence = plan_json.get("confidence", 0)
    
    text = (
        f"*Incident:* {incident_text}\n"
        f"*Confidence:* {confidence:.2f}\n\n"
        f"*Plan:*\n{steps or 'No steps provided.'}\n\n"
        f"*Risks:*\n{risks or 'No risks provided.'}"
    )

    try:
        response = client.chat_postMessage(channel=channel_id, text=text)
        return response["ts"]  # message timestamp
    except SlackApiError as e:
        print(f"Error posting to Slack: {e.response['error']}")
        return None

if __name__ == "__main__":
    # Simple test
    test_plan = {
        "steps": ["Monitor traffic", "Increase API timeout"],
        "risks": ["Might still timeout"],
        "confidence": 0.8,
    }
    ts = post_plan(SLACK_CHANNEL_ID, "AUTH-500 after login on prod", test_plan)
    if ts:
        print(f"Posted to Slack with timestamp {ts}")
