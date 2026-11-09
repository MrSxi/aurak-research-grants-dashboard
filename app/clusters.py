"""AURAK research clusters proposed by this project.

AURAK has no official research clusters, so the project defines them here. Each cluster has a name,
a short scope and seed terms, and was drafted from the departments and research interests in the
faculty dataset. Membership is never hard-coded. It is computed from each person's research evidence
(embedding similarity to the cluster scope), so it updates when publications or interests change.
Edit this list to rename, add or remove clusters. Everything else updates automatically.
"""

CLUSTERS = [
    {
        "key": "ai-data",
        "name": "Artificial Intelligence & Data Science",
        "scope": "Machine learning, deep learning, natural language processing, computer vision, data mining, "
                 "data analytics, information retrieval, knowledge representation and AI applications.",
        "terms": ["machine learning", "deep learning", "natural language processing", "computer vision",
                  "data science", "data mining", "neural networks", "artificial intelligence"],
        "color": "fbg-red",
    },
    {
        "key": "cyber-networks",
        "name": "Cybersecurity, Networks & IoT",
        "scope": "Cybersecurity, cryptography, wireless and sensor networks, vehicular networks, Internet of Things, "
                 "cloud-edge computing, embedded systems and smart communication systems.",
        "terms": ["cybersecurity", "cryptography", "wireless networks", "internet of things", "embedded systems",
                  "cloud computing", "sensor networks"],
        "color": "fbg-dark",
    },
    {
        "key": "energy-environment",
        "name": "Sustainable Energy, Water & Environment",
        "scope": "Renewable and solar energy, energy efficiency, green hydrogen, carbon capture, desalination, "
                 "water and wastewater treatment, climate change, circular economy and environmental engineering.",
        "terms": ["renewable energy", "solar energy", "energy efficiency", "carbon capture", "desalination",
                  "water treatment", "climate change", "sustainability"],
        "color": "fbg-gold",
    },
    {
        "key": "smart-cities",
        "name": "Smart Cities, Infrastructure & Built Environment",
        "scope": "Architecture, urban planning, civil and structural engineering, concrete and construction materials, "
                 "transportation and mobility, digital construction, computational design and heritage conservation.",
        "terms": ["urban planning", "architecture", "concrete", "construction", "transportation", "smart mobility",
                  "computational design", "green building"],
        "color": "fbg-dark",
    },
    {
        "key": "robotics-systems",
        "name": "Robotics, Control & Intelligent Systems",
        "scope": "Robotics, mechatronics, control systems, power electronics, electric drives, signal processing, "
                 "UAVs, automotive systems and industrial systems optimization.",
        "terms": ["robotics", "control systems", "power electronics", "mechatronics", "signal processing", "UAV"],
        "color": "fbg-red",
    },
    {
        "key": "chem-materials",
        "name": "Chemical, Petroleum & Advanced Materials",
        "scope": "Chemical and process engineering, catalysis, porous materials, nanoparticles, membranes, polymers, "
                 "petroleum and drilling engineering, reactor design and separation processes.",
        "terms": ["catalysis", "nanomaterials", "membranes", "polymers", "petroleum engineering", "drilling",
                  "reactor design"],
        "color": "fbg-gold",
    },
    {
        "key": "health-biotech",
        "name": "Health, Biotechnology & Life Sciences",
        "scope": "Biotechnology, molecular and cell biology, cell signalling, cancer biology, pharmacology and drug "
                 "discovery, metabolomics, microbiology, antibiotic resistance, genetics, plant and agricultural "
                 "biotechnology, biomedical research, diagnostics and public health.",
        "terms": ["biotechnology", "cell biology", "cancer", "pharmacology", "microbiology", "antimicrobial resistance",
                  "genetics", "metabolomics", "plant biology", "biomedical", "public health"],
        "color": "fbg-red",
    },
    {
        "key": "math-physics",
        "name": "Mathematics, Physics & Computational Science",
        "scope": "Pure and applied mathematics, statistics, mathematical modelling, numerical methods, optimization, "
                 "physics, materials physics and computational science.",
        "terms": ["mathematics", "statistics", "mathematical modelling", "physics", "numerical analysis",
                  "optimization"],
        "color": "fbg-dark",
    },
    {
        "key": "business-economy",
        "name": "Business, Finance & Digital Economy",
        "scope": "Management, leadership, entrepreneurship, marketing, accounting, finance, economics, supply chains, "
                 "project management, business intelligence and digital transformation.",
        "terms": ["management", "finance", "accounting", "marketing", "entrepreneurship", "supply chain",
                  "leadership", "economics"],
        "color": "fbg-gold",
    },
    {
        "key": "society-education",
        "name": "Education, Languages & Society",
        "scope": "Education and pedagogy, English language and academic writing, linguistics, media and communication, "
                 "psychology, history, culture, social sciences and public policy in the UAE and Gulf region.",
        "terms": ["education", "academic writing", "linguistics", "communication", "psychology", "social sciences",
                  "public policy", "culture"],
        "color": "fbg-dark",
    },
]

# A person joins every cluster whose similarity is within this margin of their best cluster
# and at least CLUSTER_MIN (so interdisciplinary researchers appear in more than one cluster).
CLUSTER_MIN = 0.22
CLUSTER_SECONDARY_MARGIN = 0.03
GRANT_CLUSTER_MIN = 0.22
