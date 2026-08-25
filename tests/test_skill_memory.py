import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.memory import WorkflowManager

def test_workflow_skill_memory():
    print("--------------------------------------------------")
    print("🧠 Testing 0xItachi Skill & Procedural Memory Storage...")
    print("--------------------------------------------------")

    wm = WorkflowManager()

    # 1. Create a dummy task history simulating successful execution
    class DummyModelOutput:
        def model_dump(self):
            return {
                "action": [
                    {"go_to_url": {"url": "https://www.saucedemo.com"}},
                    {"input_text": {"index": 0, "text": "standard_user"}},
                    {"input_text": {"index": 1, "text": "secret_sauce"}},
                    {"click_element_by_index": {"index": 2}}
                ]
            }

    class DummyHistory:
        all_model_outputs = [DummyModelOutput()]

    task_goal = "Log in to SauceDemo with standard_user and secret_sauce"

    print(f"\n1. Storing learned skill for task: '{task_goal}'")
    wm.save_workflow_from_history(task_goal, DummyHistory())

    print("\n2. Searching for stored skill matching query 'saucedemo login'...")
    match = wm.find_matching_workflow("saucedemo login standard_user")

    if match:
        print(f"✅ SKILL MEMORY MATCH FOUND!")
        print(f"   Workflow Name: {match['name']}")
        print(f"   Confidence Score: {match['confidence_score']*100:.0f}%")
        print(f"   Total Runs: {match['runs']}")

        print("\n3. Generated Prompt Injection Hint:")
        hint = wm.format_workflow_hint(match)
        print(hint)
        print("\n--------------------------------------------------")
        print("🎉 Skill Memory Test PASSED Successfully!")
        print("--------------------------------------------------")
    else:
        print("❌ Skill Memory Search Failed!")

if __name__ == "__main__":
    test_workflow_skill_memory()
