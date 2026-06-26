import sys
sys.path.insert(0, "/ai/data/repos/work-manuscript/src")
sys.path.insert(0, "/ai/data/repos/work-manuscript/scripts")
try:
    from materials2textbook.agents.domain_config_agent import DomainConfigAgent
    print("domain_config_agent OK")
except Exception as e:
    print(f"domain_config_agent FAIL: {e}")

try:
    from materials2textbook.agents.book_plan_llm import BookPlanLLMAgent
    print("book_plan_llm OK")
except Exception as e:
    print(f"book_plan_llm FAIL: {e}")

try:
    from materials2textbook.domain_config import load_domain_config
    print("domain_config OK")
except Exception as e:
    print(f"domain_config FAIL: {e}")

try:
    from scripts.run_topic_textbook import main
    print("run_topic_textbook OK")
except Exception as e:
    print(f"run_topic_textbook FAIL: {e}")
