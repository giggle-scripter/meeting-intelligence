"""Keep automated tests isolated from developer and deployment credentials."""

import os


# pytest runs in a child process of the local OpenAI smoke script. Removing
# provider/auth settings here cannot change the parent terminal environment;
# it only prevents API tests from enabling auth or making paid provider calls.
for environment_name in (
    "POWER_AUTOMATE_API_KEY",
    "OPENAI_API_KEY",
    "AI_FALLBACK_ENDPOINT",
    "AI_FALLBACK_API_KEY",
    "AZURE_AI_FOUNDRY_CHAT_ENDPOINT",
    "AZURE_AI_FOUNDRY_API_KEY",
):
    os.environ.pop(environment_name, None)
