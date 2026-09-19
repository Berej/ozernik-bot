"""
Simple test script to verify RSN module integrity
Run: python test_module.py (from modules/rsn/ directory or root)
"""

import sys
from pathlib import Path

def test_imports():
    """Test that all modules can be imported."""
    try:
        from rsn_db import RsnDatabase
        from rsn_config import RsnConfig
        print("✓ Imports successful")
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False

def test_db_init():
    """Test database initialization."""
    try:
        import tempfile
        from rsn_db import RsnDatabase
        
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
        
        db.close()
        Path(db_path).unlink()
        print("✓ Database operations successful")
        return True
    except Exception as e:
        print(f"✗ Database test failed: {e}")
        return False

def test_config_init():
    """Test configuration initialization."""
    try:
        import tempfile
        from rsn_config import RsnConfig
        
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            config_path = f.name
        
        cfg = RsnConfig(config_path)
        
        # Test getters
        assert cfg.get_scale_max() == 50
        assert cfg.get_weekly_point_gain() == 1
        assert cfg.get_reset_points_on_return() == 10
        
        # Test multiplier calculation
        assert cfg.get_multiplier_for_points(50) == 1  # isolator
        assert cfg.get_multiplier_for_points(20) == 2  # restricted
        assert cfg.get_multiplier_for_points(5) == 3   # critical
        assert cfg.get_multiplier_for_points(0) == 3   # ban_trigger
        
        # Test setters
        cfg.set_log_channel_id(12345)
        assert cfg.get_log_channel_id() == 12345
        
        Path(config_path).unlink()
        print("✓ Configuration operations successful")
        return True
    except Exception as e:
        print(f"✗ Configuration test failed: {e}")
        return False

def test_syntax():
    """Test Python syntax of all modules."""
    try:
        import ast
        
        for module_file in ['rsn_db.py', 'rsn_config.py', 'rsn.py']:
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
        ("Config Init & Ops", test_config_init),
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
