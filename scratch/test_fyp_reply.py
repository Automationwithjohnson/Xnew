import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import auto_like_comment

if __name__ == "__main__":
    print("=== TESTING FYP AUTO-COMMENTER LOCALLY (1 FYP REPLY ONLY) ===")
    sys.argv = ["auto_like_comment.py", "--test"]
    asyncio.run(auto_like_comment.main())
