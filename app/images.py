"""Photos from aurak.ac.ae used on the dashboard's cards (loaded by the visitor's browser, like the logo).

Every image was taken from the AURAK page on the same topic (department, programme or research centre page).
"""
from urllib.parse import urlparse

MEDIA = "https://aurak.ac.ae/storage/media/"

# One photo per research cluster (key from app/clusters.py)
CLUSTER_IMAGES = {
    "ai-data": MEDIA + "artifical-intelligence.jpg",                   # BS in Artificial Intelligence
    "cyber-networks": MEDIA + "cyber-security-1.jpg",                   # Computer Science & Engineering
    "energy-environment": MEDIA + "master-of-sustaibalef.jpg",          # MSc Sustainable and Renewable Energy
    "smart-cities": MEDIA + "bachelor-of-architecture-banner.jpg",      # Architecture and Civil Engineering
    "robotics-systems": MEDIA + "bseee-robotics.jpg",                   # Mechanical, Electrical & Intelligent Systems
    "chem-materials": MEDIA + "chemical-2.jpg",                         # Chemical and Petroleum Engineering
    "health-biotech": MEDIA + "biotechnology-2.jpg",                    # Biotechnology
    "math-physics": MEDIA + "department-of-mathematics-and-natural-sciencesf.jpg",  # Mathematics and Physics
    "business-economy": MEDIA + "bsba-business-analytics.jpg",          # Business Analytics
    "society-education": MEDIA + "dept-of-humanties-1.jpg",             # Humanities and Social Sciences
}

# Home page tiles and hero
PAGE_IMAGES = {
    "hero": MEDIA + "campus.jpg",
    "uae": MEDIA + "rakric-bannerf.jpg",                     # RAK Research & Innovation Center
    "international": MEDIA + "international-student-program.jpg",
    "closing": MEDIA + "research-and-innovation.jpg",
}

# Rotating pool for grant tiles (news / events lists); picked by grant id so a grant keeps its photo
GRANT_POOL = [
    MEDIA + "soec.jpg", MEDIA + "ataic.jpg", MEDIA + "research-and-innovation.jpg", MEDIA + "campus.jpg",
    MEDIA + "sustainability-6.jpg", MEDIA + "rakric-bannerf.jpg", MEDIA + "bscivil.jpg",
    MEDIA + "biotechnology-2.jpg", MEDIA + "artifical-intelligence.jpg",
]

# aurak.ac.ae/robots.txt asks that these files not be used (checked 26 Oct 2026); the dashboard shows initials instead.
DISALLOWED_PATHS = {
    "/storage/media/lobna-nassar.jpg", "/storage/media/lobna-nassar-600w.jpg",
    "/storage/media/lobna-nassar-1200w.jpg", "/storage/media/lobna-nassar-1.jpg",
}


def grant_image(grant_id: int) -> str:
    return GRANT_POOL[(grant_id or 0) % len(GRANT_POOL)]


def image_allowed(url: str | None) -> bool:
    return bool(url) and urlparse(url).path not in DISALLOWED_PATHS
