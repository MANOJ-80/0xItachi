import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.memory import WorkflowManager

def test_workflow_manager():
    wm = WorkflowManager()
    
    # Mock history object
    class MockOutput:
        def model_dump(self):
            return {"action": [{"go_to_url": {"url": "https://news.ycombinator.com"}}]}
            
    class MockHistory:
        all_model_outputs = [MockOutput()]
        
    goal = "Go to news.ycombinator.com, read the top 3 headlines, and summarize what they are about."
    
    print("Testing saving workflow...")
    wm.save_workflow_from_history(goal, MockHistory())
    
    print("Testing finding workflow...")
    match = wm.find_matching_workflow("Summarize Hacker News top headlines")
    if match:
        print("✅ Workflow Match Found!")
        print(wm.format_workflow_hint(match))
    else:
        print("❌ Match Failed!")

if __name__ == "__main__":
    test_workflow_manager()
