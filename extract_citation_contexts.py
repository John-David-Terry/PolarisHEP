import sqlite3, os, re
from lxml import etree

DB = "inspire.sqlite"
TEI_DIR = "data/tei/citers"

NS = {"tei": "http://www.tei-c.org/ns/1.0"}

def norm(x):
    return re.sub(r"\s+", "", x.lower()) if x else None

conn = sqlite3.connect(DB)
cur = conn.cursor()

# Load lookup: arXiv/DOI -> parent_cn
lookup = []
for parent_cn, arxiv_id, doi in cur.execute(
    "SELECT parent_cn, arxiv_id_norm, doi_norm FROM top200_lookup"
):
    lookup.append((parent_cn, arxiv_id, doi))

for fname in os.listdir(TEI_DIR):
    if not fname.endswith(".tei.xml"):
        continue
    child_cn = int(fname.replace(".tei.xml",""))
    path = os.path.join(TEI_DIR, fname)
    root = etree.parse(path).getroot()

    # map ref id -> (arxiv, doi)
    refs = {}
    for b in root.xpath(".//tei:listBibl/tei:biblStruct", namespaces=NS):
        rid = b.get("{http://www.w3.org/XML/1998/namespace}id")
        arxiv = b.xpath(".//tei:idno[@type='arXiv']/text()", namespaces=NS)
        doi   = b.xpath(".//tei:idno[@type='DOI']/text()", namespaces=NS)
        refs[rid] = (norm(arxiv[0]) if arxiv else None,
                     norm(doi[0]) if doi else None)

    for ref in root.xpath(".//tei:ref[@type='bibr']", namespaces=NS):
        rid = ref.get("target","").replace("#","")
        if rid not in refs:
            continue
        arxiv, doi = refs[rid]

        for parent_cn, a_id, d_id in lookup:
            if (a_id and arxiv == a_id) or (d_id and doi == d_id):
                sent = ref.xpath("string(ancestor::tei:p)", namespaces=NS)
                cur.execute(
                    "INSERT INTO citation_mentions(child_cn,parent_cn,sentence) VALUES (?,?,?)",
                    (child_cn, parent_cn, sent.strip())
                )

conn.commit()
conn.close()
