import json
from Mark import MarkAssistant, load_config

cfg = load_config()
# ensure we don't call external n8n in tests
cfg['N8N_WEBHOOK_URL'] = ''
cfg['AUTONOMY_LEVEL'] = 'manual'
cfg['ALLOWLIST'] = 'search_leads,create_proposal,send_email'

assistant = MarkAssistant(cfg)
res = assistant.execute_action('search_leads', {'query':'python developer remote', 'limit':2})
print(json.dumps(res, ensure_ascii=False, indent=2))
