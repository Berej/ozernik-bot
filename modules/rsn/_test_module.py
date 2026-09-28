"""
Simple test script to verify RSN module integrity
Run: python test_module.py (from modules/rsn/ directory or root)
"""

import sys
from pathlib import Path

def test_imports():
    """Test that all modules can be imported."""
    try:
        from _rsn_db import RsnDatabase
        print("✓ Imports successful")
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False

def test_db_init():
    """Test database initialization."""
    try:
        import tempfile
        from _rsn_db import RsnDatabase
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        db = RsnDatabase(db_path)
        
        # Test score operations
        db.ensure_score(12345)
        score = db.get_score(12345)
        assert score is not None
        assert score['points'] == 50
        
        # Test points manipulation
        db.set_points(12345, 25)
        score = db.get_score(12345)
        assert score['points'] == 25
        
        # Test active flag
        db.set_active(12345, 1)
        score = db.get_score(12345)
        assert score['active'] == 1

        # Test record + timer (active punishment) operations
        record_id = db.create_record(12345, "mute", "test", 1.5, -5, 999)
        assert record_id > 0
        db.create_or_update_punishment(12345, "mute", 0, record_id)
        active = db.get_active_punishment(12345)
        assert active is not None and active['kind'] == 'mute'
        assert db.get_expired_punishments()
        db.delete_active_punishment(12345)
        assert db.get_active_punishment(12345) is None
        
        db.close()
        Path(db_path).unlink()
        print("✓ Database operations successful")
        return True
    except Exception as e:
        print(f"✗ Database test failed: {e}")
        return False

def test_rsn_json():
    # Check that rsn_data.json is valid JSON with required keys
    try:
        import json
        data = json.loads(Path("rsn_data.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        for key in ("mute_role_id", "log_channel_id", "admin_role_ids", "moderator_role_ids"):
            assert key in data, "missing key: " + key
        print("OK: rsn_data.json parsed")
        return True
    except Exception as e:
        print("FAIL: rsn_data.json " + str(e))
        return False
def test_syntax():
    """Test Python syntax of all modules."""
    try:
        import ast
        
        for module_file in ["_rsn_db.py", "rsn.py"]:
            with open(module_file) as f:
                ast.parse(f.read())
        
        print("✓ Syntax check successful")
        return True
    except Exception as e:
        print(f"✗ Syntax check failed: {e}")
        return False

if __name__ == '__main__':
    print("=" * 50)
    print("RSN Module Test Suite")
    print("=" * 50)
    
    tests = [
        ("Syntax Check", test_syntax),
        ("Module Imports", test_imports),
        ("Database Init & Ops", test_db_init),
        ("RSN Config JSON", test_rsn_json),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n[{len(results) + 1}] {test_name}...")
        results.append(test_func())
    
    print("\n" + "=" * 50)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("✓ All tests passed!")
        sys.exit(0)
    else:
        print("✗ Some tests failed")
        sys.exit(1)
