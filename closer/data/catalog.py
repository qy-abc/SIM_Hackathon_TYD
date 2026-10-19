"""The shop's product list: (sku, name, category, brand, unit price in cents).

Reference data, like staff.csv: the generator sells from it and the loader reads
roadshow item text back into line items with it.
"""
CATALOG = (
    ("MT-SLQ", "Slumber Queen Mattress", "mattress", "Slumber", 189900),
    ("MT-SLK", "Slumber King Mattress", "mattress", "Slumber", 239900),
    ("MT-RWQ", "RestWell Queen Mattress", "mattress", "RestWell", 129900),
    ("PL-SL2", "Slumber Pillow Pair", "accessory", "Slumber", 9900),
    ("SF-OS3", "Oslo 3-Seater Sofa", "sofa", "Nordhaus", 219900),
    ("SF-BGL", "Bergen L-Shape Sofa", "sofa", "Nordhaus", 349900),
    ("RC-ARM", "Recliner Armchair", "sofa", "Nordhaus", 99900),
    ("BF-KYQ", "Kyoto Queen Bedframe", "bedframe", "Hinoki", 89900),
    ("DT-TK6", "Teak 6-Seater Dining Table", "dining", "Hinoki", 159900),
    ("CH-DIN", "Dining Chair", "dining", "Hinoki", 19900),
    ("WR-3DR", "3-Door Wardrobe", "wardrobe", "Nordhaus", 119900),
    ("TV-CON", "TV Console", "living", "Hinoki", 69900),
)
