from webagent.integrations.agentsmith import AgentSmithEnvironment

def test_numbered_keys_precede_legacy_duplicate():
    e=AgentSmithEnvironment(secrets={"GROQ_API_KEY":"a","GROQ_API_KEY_2":"b","GROQ_API_KEY_1":"a"})
    assert e.groq_keys==["a","b"]
