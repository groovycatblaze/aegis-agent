"""Reset the simulated enterprise systems to their seed state."""
from core import enterprise

if __name__ == "__main__":
    print("seeded", enterprise.seed())
