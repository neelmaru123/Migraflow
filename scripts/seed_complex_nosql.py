"""
Complex NoSQL Enterprise Database Seeder
Creates a sophisticated MongoDB database ('complex_nosql_enterprise') demonstrating advanced NoSQL features
that present extreme impedance mismatch and structural difficulty when migrating/converting to relational SQL.

Features Included:
1. Deeply Nested Hierarchies (Level 5-7 nesting).
2. Radical Schema Polymorphism (Heterogeneous document shapes in a single collection).
3. Dynamic Field Typing (Same field holds Integer, String, Boolean, Array, or Object across documents).
4. Arrays of Arrays (Multi-dimensional / Jagged Arrays) and Deeply Nested Sub-arrays.
5. GeoJSON Spatial Objects (Points, LineStrings, Polygons with 2dsphere indexing).
6. Arbitrary / Unbounded Dynamic Key-Value Maps (No fixed schema or column definition).
7. Native BSON Types (Decimal128, Binary UUID, ISODate, Regex patterns, ObjectIds).
"""

import sys
import random
import uuid
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pymongo
from bson import ObjectId, Decimal128, Binary, Regex

MONGO_URL = "mongodb://localhost:27017/"
DB_NAME = "complex_nosql_enterprise"

def get_mongo_client():
    return pymongo.MongoClient(MONGO_URL)

def seed_database():
    client = get_mongo_client()
    # Drop existing database to ensure clean, repeatable state
    client.drop_database(DB_NAME)
    db = client[DB_NAME]
    print(f"=== INITIALIZING HIGHLY COMPLEX NOSQL DATABASE: '{DB_NAME}' ===")

    # =========================================================================
    # COLLECTION 1: smart_iot_fleet (Level 5-7 Deeply Nested Hierarchy)
    # =========================================================================
    print("\n>>> Seeding 'smart_iot_fleet' (Level 5-7 Hierarchical Nesting & GeoJSON)...")
    fleet_docs = []
    base_time = datetime.now(timezone.utc) - timedelta(days=60)

    vehicle_models = ["AeroDrone-X9", "AutonomousHauler-V4", "DeepSeaProbe-Echo", "SubOrbitalGlider-Z", "RoboRover-Titan"]
    cities = [
        {"name": "San Francisco", "coords": [-122.4194, 37.7749]},
        {"name": "Tokyo", "coords": [139.6917, 35.6895]},
        {"name": "Berlin", "coords": [13.4050, 52.5200]},
        {"name": "Sydney", "coords": [151.2093, -33.8688]},
        {"name": "Reykjavik", "coords": [-21.9426, 64.1466]}
    ]

    for i in range(1, 151):
        city = cities[i % len(cities)]
        lon = city["coords"][0] + (random.random() - 0.5) * 0.2
        lat = city["coords"][1] + (random.random() - 0.5) * 0.2

        # Level 1: Root
        doc = {
            "_id": f"FLEET-NODE-{i:04d}",
            "hardware_vin": f"VIN-HEX-{uuid.uuid4().hex[:12].upper()}",
            "model": vehicle_models[i % len(vehicle_models)],
            "operational_status": random.choice(["ACTIVE", "MISSION_CRITICAL", "MAINTENANCE", "STANDBY"]),
            "manufacture_date": base_time + timedelta(days=i * 2),
            "asset_cost_usd": Decimal128(f"{round(45000.00 + (i * 325.50), 2)}"),
            
            # GeoJSON Spatial Object (Feature 5)
            "current_telemetry_location": {
                "type": "Point",
                "coordinates": [round(lon, 6), round(lat, 6)]
            },
            
            # Level 2: Architecture Stack
            "architecture": {
                "firmware_version": f"v{2 + (i % 5)}.{i % 12}.{i % 8}",
                "kernel_build": Binary(uuid.uuid4().bytes, 4), # BSON Binary UUID
                "subsystems": {
                    # Level 3: Powertrain Subsystem
                    "powertrain": {
                        "bus_standard": "CAN-FD-Bus-Extended",
                        "primary_inverter": {
                            # Level 4: Control Unit
                            "control_unit": {
                                "mcu_serial": f"MCU-SN-{100000 + i}",
                                "realtime_diagnostics": {
                                    # Level 5: Combustion/Motor Matrix
                                    "chamber_telemetry": {
                                        "sampling_rate_hz": 2500,
                                        "channel_group": f"GRP-{(i % 4) + 1}",
                                        "thermal_zones": {
                                            # Level 6: Sensor arrays
                                            "manifold_nodes": [
                                                {
                                                    "sensor_id": f"SNS-M-01-{i}",
                                                    "nominal_celsius": 185.4 + (i % 20),
                                                    # Level 7: Calibration Matrix
                                                    "calibration": {
                                                        "drift_factor": round(1.0024 + (i * 0.0001), 6),
                                                        "poly_coefficients": [round(0.0012 * j, 4) for j in range(1, 5)],
                                                        "active_compensation_matrix": [
                                                            [1.0, 0.0, 0.0],
                                                            [0.0, round(1.0 + (i * 0.002), 4), 0.0],
                                                            [round(0.01 * (i % 5), 3), 0.0, 1.0]
                                                        ],
                                                        "last_certified_inspector": {
                                                            "tech_id": f"TECH-{900 + (i % 50)}",
                                                            "laboratory": "Aerospace Micro-Systems Metrology Lab"
                                                        }
                                                    }
                                                },
                                                {
                                                    "sensor_id": f"SNS-M-02-{i}",
                                                    "nominal_celsius": 192.1 + (i % 15),
                                                    "calibration": {
                                                        "drift_factor": 0.9984,
                                                        "poly_coefficients": [0.002, 0.005, 0.001],
                                                        "active_compensation_matrix": [
                                                            [1.0, 0.0, 0.0],
                                                            [0.0, 1.0, 0.0],
                                                            [0.0, 0.0, 1.0]
                                                        ]
                                                    }
                                                }
                                            ]
                                        }
                                    }
                                }
                            }
                        }
                    },
                    # Dynamic Keyed Sensor Readouts (Feature 6: Unbounded Arbitrary Map)
                    "dynamic_environmental_sensors": {
                        f"ambient_sensor_channel_{100 + (j * 7)}": {
                            "reading": round(20.0 + random.random() * 50, 3),
                            "unit": random.choice(["celsius", "psi", "lux", "dBm", "ppm"]),
                            "quality_score": round(0.85 + (random.random() * 0.15), 4)
                        }
                        for j in range(1, 4 + (i % 4))
                    }
                }
            }
        }
        fleet_docs.append(doc)

    db["smart_iot_fleet"].insert_many(fleet_docs)
    db["smart_iot_fleet"].create_index([("current_telemetry_location", "2dsphere")])
    db["smart_iot_fleet"].create_index([("hardware_vin", 1)], unique=True)
    db["smart_iot_fleet"].create_index([("architecture.subsystems.powertrain.primary_inverter.control_unit.mcu_serial", 1)])
    print(f"  [OK] Seeded 'smart_iot_fleet' with {len(fleet_docs)} documents (Level 7 nesting).")

    # =========================================================================
    # COLLECTION 2: omnichannel_customer_graph (Radical Schema Polymorphism)
    # =========================================================================
    print("\n>>> Seeding 'omnichannel_customer_graph' (Polymorphic Entities & Mixed Data Types)...")
    customer_docs = []

    for i in range(1, 151):
        # We model 3 completely distinct schema personas in the same collection:
        # Persona 1: Enterprise Corporate Account (Complex corporate structure, multi-seat licenses, SLA contracts)
        # Persona 2: Individual Consumer (Loyalty points, device biometric tokens, shipping preference graph)
        # Persona 3: Anonymous / Guest Session (Temporary cart, ephemeral OAuth handshake, tracking beacons)
        persona_type = i % 3

        if persona_type == 0:
            # Corporate B2B Account
            doc = {
                "_id": f"CUST-CORP-{i:04d}",
                "entity_kind": "ENTERPRISE_ORGANIZATION",
                "org_name": f"Apex Global Conglomerate #{i}",
                "tax_id": f"EIN-{random.randint(10, 99)}-{random.randint(1000000, 9999999)}",
                # Mixed Data Type feature: 'compliance_clearance' is an Object here
                "compliance_clearance": {
                    "iso_27001": True,
                    "soc2_type_ii": True,
                    "gdpr_dpa_signed_at": base_time + timedelta(days=i),
                    "auditor_firm": "Deloitte & Touche Cyber Risk Advisory"
                },
                "contract": {
                    "annual_recurring_revenue": Decimal128(f"{round(120000.00 + (i * 1500.00), 2)}"),
                    "sla_tier": "MISSION_CRITICAL_99_999",
                    "seats_allocated": 500 + (i * 20)
                },
                # Multi-tenant Subsidiary Hierarchy (Tree structure inside single doc)
                "business_units": [
                    {
                        "unit_code": f"BU-APAC-{i}",
                        "general_manager": f"Director Sarah Lin #{i}",
                        "cost_centers": [f"CC-{1000 + i}", f"CC-{2000 + i}"],
                        "divisions": [
                            {
                                "name": "Advanced Research & Autonomous Systems",
                                "headcount": 140,
                                "allocated_ip_pools": ["10.240.0.0/16", "172.16.40.0/20"]
                            }
                        ]
                    }
                ],
                "authorized_signers": [f"exec_{j}_{i}@apex-corp.com" for j in range(1, 4)]
            }

        elif persona_type == 1:
            # Individual B2C Consumer
            doc = {
                "_id": f"CUST-INDIV-{i:04d}",
                "entity_kind": "INDIVIDUAL_CONSUMER",
                "personal_profile": {
                    "first_name": random.choice(["Elena", "Marcus", "Kavita", "Liam", "Mei"]),
                    "last_name": random.choice(["Vance", "Thorne", "Patel", "Chen", "O'Connor"]),
                    "date_of_birth": "1988-06-14",
                    "preferred_locale": "en_US"
                },
                # Mixed Data Type feature: 'compliance_clearance' is a simple Boolean here
                "compliance_clearance": True,
                # Dynamic array with polymorphic elements inside the array itself
                "payment_methods": [
                    {
                        "method_type": "CREDIT_CARD",
                        "brand": "Mastercard World Elite",
                        "last4": f"{1000 + (i % 8999)}",
                        "billing_postal": "94107"
                    },
                    {
                        "method_type": "CRYPTO_WEB3",
                        "network": "Ethereum Mainnet",
                        "wallet_address": f"0x71C...{uuid.uuid4().hex[:8]}",
                        "ens_name": f"consumer_{i}.eth"
                    }
                ],
                "loyalty_tier": {
                    "tier_name": "DIAMOND_ELITE",
                    "points_balance": 48250 + (i * 120),
                    "rewards_unlocked": ["FREE_GLOBAL_EXPEDITE", "24_7_CONCIERGE"]
                },
                # Array of nested geo-fenced delivery drops
                "preferred_drop_locations": [
                    {"label": "Penthouse Residence", "geo": [37.7833, -122.4167], "gate_code": "8492#"},
                    {"label": "Private Hangar Bay 4", "geo": [37.6213, -122.3790], "gate_code": "CLEARANCE-ALPHA"}
                ]
            }

        else:
            # Ephemeral / Guest Tracking Session
            doc = {
                "_id": f"CUST-GUEST-{i:04d}",
                "entity_kind": "ANONYMOUS_SESSION",
                "session_fingerprint": uuid.uuid4().hex,
                # Mixed Data Type feature: 'compliance_clearance' is a String status here
                "compliance_clearance": "PENDING_COOKIE_BANNER_ACCEPTANCE",
                "funnel_telemetry": {
                    "referrer": "https://news.ycombinator.com/item?id=389104",
                    "utm_campaign": "q3_ai_transformation_blitz",
                    "ad_impression_id": Binary(uuid.uuid4().bytes, 0)
                },
                # Unstructured, variable bag of cookies & client parameters
                "client_environment": {
                    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "screen_resolution": [2560, 1440],
                    "color_depth": 32,
                    "webgl_vendor": "Apple Inc. (Apple M3 Max)",
                    "supported_codecs": ["av01", "vp09", "h265"]
                },
                "ephemeral_cart_contents": [
                    {"sku": f"SKU-{100 + j}", "qty": j, "unit_price": Decimal128("89.50")}
                    for j in range(1, 3)
                ]
            }

        customer_docs.append(doc)

    db["omnichannel_customer_graph"].insert_many(customer_docs)
    db["omnichannel_customer_graph"].create_index([("entity_kind", 1)])
    db["omnichannel_customer_graph"].create_index([("contract.annual_recurring_revenue", 1)], sparse=True)
    db["omnichannel_customer_graph"].create_index([("personal_profile.last_name", 1)], sparse=True)
    print(f"  [OK] Seeded 'omnichannel_customer_graph' with {len(customer_docs)} polymorphic documents.")

    # =========================================================================
    # COLLECTION 3: clinical_genomics_records (Arrays of Arrays & EHR Sub-Trees)
    # =========================================================================
    print("\n>>> Seeding 'clinical_genomics_records' (Multi-dimensional Arrays & Clinical EHR)...")
    clinical_docs = []

    for i in range(1, 121):
        doc = {
            "_id": f"PATIENT-EHR-{i:04d}",
            "mrn_hash": f"SHA256-{uuid.uuid4().hex}",
            "recorded_at": base_time + timedelta(days=i),
            
            # Clinical Encounter Graph (Nested array containing nested arrays of lab panels)
            "encounters": [
                {
                    "encounter_id": f"ENC-{20260000 + (i * 10) + enc_idx}",
                    "facility": f"Metropolitan Academic Medical Center - Site {chr(65 + (enc_idx % 4))}",
                    "attending_physician": {
                        "npi": f"NPI-{1849000000 + i}",
                        "specialty": "Molecular Oncology & Precision Therapeutics"
                    },
                    "vitals": {
                        "systolic": 118 + (i % 20),
                        "diastolic": 76 + (i % 12),
                        "pulse_bpm": 68 + (i % 18),
                        "oxygen_saturation_pct": 98.5
                    },
                    # Multi-tiered nested array
                    "diagnostic_evaluations": [
                        {
                            "panel_name": "Comprehensive Solid Tumor Genomic Biomarker Assay",
                            "sequencer_model": "Illumina NovaSeq X Plus",
                            "read_depth_coverage": 1250,
                            # Array of structured variant findings
                            "detected_variants": [
                                {
                                    "gene_symbol": gene,
                                    "chromosome": f"chr{random.randint(1, 22)}",
                                    "hgvs_cdna": f"c.{random.randint(100, 3000)}G>A",
                                    "hgvs_protein": f"p.{random.choice(['Arg', 'Val', 'Leu', 'Gly'])}{random.randint(50, 900)}del",
                                    "variant_allele_frequency": round(0.12 + (random.random() * 0.45), 4),
                                    "pathogenicity_classification": random.choice([
                                        "PATHOGENIC",
                                        "LIKELY_PATHOGENIC",
                                        "VARIANT_OF_UNCERTAIN_SIGNIFICANCE"
                                    ]),
                                    # Feature 4: Array of Arrays (2D Matrix of Quality Metrics)
                                    "exon_quality_matrix": [
                                        [round(30.0 + random.random() * 15, 1) for _ in range(4)],
                                        [round(28.0 + random.random() * 14, 1) for _ in range(4)],
                                        [round(32.0 + random.random() * 12, 1) for _ in range(4)]
                                    ],
                                    # Sub-array of therapeutic match hypotheses
                                    "actionable_clinical_trials": [
                                        {
                                            "nct_id": f"NCT0{5400000 + random.randint(1000, 9999)}",
                                            "phase": random.choice(["Phase II", "Phase III"]),
                                            "drug_candidate": f"TX-INHIBITOR-{random.randint(100, 999)}",
                                            "eligibility_match_score": round(0.91 + (random.random() * 0.08), 3)
                                        }
                                    ]
                                }
                                for gene in random.sample(["EGFR", "KRAS", "BRAF", "PIK3CA", "TP53", "BRCA1", "ALK"], 2)
                            ]
                        }
                    ]
                }
                for enc_idx in range(1, 3)
            ],

            # Unbounded EAV-style phenotype flags
            "phenotypic_hpo_terms": {
                f"HP:{3000000 + (k * 13)}": {
                    "term_label": f"Phenotypic Manifestation Vector #{k}",
                    "onset_age_months": 12 * (20 + (i % 40)),
                    "is_present": True
                }
                for k in range(1, 5)
            }
        }
        clinical_docs.append(doc)

    db["clinical_genomics_records"].insert_many(clinical_docs)
    db["clinical_genomics_records"].create_index([("mrn_hash", 1)], unique=True)
    db["clinical_genomics_records"].create_index([("encounters.diagnostic_evaluations.detected_variants.gene_symbol", 1)])
    print(f"  [OK] Seeded 'clinical_genomics_records' with {len(clinical_docs)} complex clinical documents.")

    # =========================================================================
    # COLLECTION 4: polymorphic_event_bus (Mixed Types, Regex, Dynamic Payloads)
    # =========================================================================
    print("\n>>> Seeding 'polymorphic_event_bus' (Dynamic Typings, Regex, Multi-Schema Payloads)...")
    event_docs = []

    event_kinds = [
        "PAYMENT_AUTHORIZED",
        "SECURITY_FIREWALL_BREACH",
        "USER_LOGIN_MFA",
        "DATABASE_SCHEMA_DIFF",
        "CONTAINER_ORCHESTRATION_EVENT",
        "AI_INFERENCE_TRACE"
    ]

    for i in range(1, 201):
        event_kind = event_kinds[i % len(event_kinds)]
        base_event = {
            "_id": ObjectId(),
            "event_uuid": uuid.uuid4().hex,
            "topic": f"enterprise.telemetry.{event_kind.lower()}",
            "kind": event_kind,
            "occurred_at": base_time + timedelta(minutes=i * 25),
            "cluster_node": f"k8s-worker-node-{chr(97 + (i % 8))}.prod.internal",
        }

        # Dynamic payload that varies wildly based on event_kind
        if event_kind == "PAYMENT_AUTHORIZED":
            base_event["payload"] = {
                "transaction_id": f"TXN-{uuid.uuid4().hex[:10].upper()}",
                "amount": Decimal128(f"{round(19.99 + (i * 4.25), 2)}"),
                "currency": random.choice(["USD", "EUR", "GBP", "JPY"]),
                "fraud_risk_score": round(random.random() * 100, 2),
                # Dynamic typing check: 'verification_code' is an INT
                "verification_code": 849201,
                "split_payouts": [
                    {"vendor_id": f"V-{100 + j}", "payout_cut": Decimal128("12.50")}
                    for j in range(1, 3)
                ]
            }

        elif event_kind == "SECURITY_FIREWALL_BREACH":
            base_event["payload"] = {
                "severity": "SEV_1_CRITICAL",
                "attacker_ip": f"198.51.100.{1 + (i % 250)}",
                # Regex stored as native BSON Regex (Feature 7)
                "matching_firewall_rule": Regex(r"^(UNION\s+SELECT|DROP\s+TABLE|1=1)", "i"),
                # Dynamic typing check: 'verification_code' is a STRING here
                "verification_code": "BLOCKED_BY_WAF_CHALLENGE_CAPTCHA",
                "attack_payload_preview": "GET /api/v1/auth?token=' OR '1'='1' -- HTTP/1.1",
                "geo_origin": {
                    "country": "Anonymous Proxy / Tor Exit Node",
                    "autonomous_system": "AS9009 Tor Relay Network"
                }
            }

        elif event_kind == "AI_INFERENCE_TRACE":
            base_event["payload"] = {
                "model_name": "claude-3-7-sonnet-quantum",
                "prompt_tokens": 1420 + (i * 15),
                "completion_tokens": 680 + (i * 8),
                # Dynamic typing check: 'verification_code' is a DICT here
                "verification_code": {
                    "signature": "ecdsa-p256-sha256",
                    "key_id": "kms-key-vault-01"
                },
                "reasoning_steps": [
                    {
                        "step_index": s_idx,
                        "action": f"Vector search similarity scan in collection chunk #{s_idx}",
                        "sub_thoughts": [
                            f"Evaluated candidate hypothesis {s_idx}.a against knowledge item constraints",
                            f"Pruned invalid search branches with threshold 0.92"
                        ]
                    }
                    for s_idx in range(1, 4)
                ],
                # Nested tensor-like array
                "embedding_head": [round(random.random() - 0.5, 4) for _ in range(8)]
            }

        elif event_kind == "CONTAINER_ORCHESTRATION_EVENT":
            base_event["payload"] = {
                "namespace": "production-data-platform",
                "pod_name": f"etl-worker-daemon-{uuid.uuid4().hex[:6]}",
                "exit_code": 0 if i % 4 != 0 else 137, # OOMKilled vs Clean
                # Dynamic typing check: 'verification_code' is a BOOLEAN here
                "verification_code": True,
                "cgroup_memory_limit_bytes": 4294967296,
                "oom_killed": (i % 4 == 0)
            }

        else:
            # Fallback event
            base_event["payload"] = {
                "message": f"Generic operational event dispatch for sequence #{i}",
                # Dynamic typing check: 'verification_code' is an ARRAY here
                "verification_code": ["CODE_ALPHA", "CODE_BETA", 404]
            }

        event_docs.append(base_event)

    db["polymorphic_event_bus"].insert_many(event_docs)
    db["polymorphic_event_bus"].create_index([("topic", 1)])
    db["polymorphic_event_bus"].create_index([("occurred_at", -1)])
    db["polymorphic_event_bus"].create_index([("kind", 1), ("occurred_at", -1)])
    print(f"  [OK] Seeded 'polymorphic_event_bus' with {len(event_docs)} dynamic events.")

    # =========================================================================
    # SUMMARY & PROFILING REPORT
    # =========================================================================
    print("\n" + "=" * 80)
    print("COMPLEX NOSQL DATABASE CREATION COMPLETE!")
    print(f"Database Name: '{DB_NAME}'")
    print("=" * 80)

    collections = db.list_collection_names()
    total_docs = 0
    for coll_name in sorted(collections):
        count = db[coll_name].count_documents({})
        total_docs += count
        print(f"  * Collection: {coll_name:30s} -> {count:5d} documents")

    print(f"\nTotal Complex NoSQL Documents Created: {total_docs:,} across {len(collections)} collections.")
    print("=" * 80)

if __name__ == "__main__":
    seed_database()
