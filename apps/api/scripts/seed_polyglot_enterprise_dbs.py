"""
Polyglot Enterprise Database Seeder
Initializes and populates 3 intra-connected databases with strong internal foreign keys:
1. PostgreSQL (Port 5435, DB: retail_commerce_pg) - Core commerce & financial transactions
2. MySQL      (Port 3307, DB: retail_logistics_mysql) - Supply chain, warehousing & fulfillment
3. MongoDB    (Port 27017, DB: retail_experience_mongo) - Customer experience, telemetry & support tickets

Cross-System Referential Keys:
- customer_id: PostgreSQL (customers) <-> MongoDB (customer_profiles, support_tickets, product_reviews, session_events)
- order_id: PostgreSQL (orders) <-> MySQL (shipment_consignments) <-> MongoDB (support_tickets)
- product_sku: MySQL (products_catalog) <-> PostgreSQL (order_items) <-> MongoDB (product_reviews, session_events)
"""

import time
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import create_engine, text
from pymongo import MongoClient

# ---------------------------------------------------------------------------
# Connection URLs
# ---------------------------------------------------------------------------
PG_ADMIN_URL = "postgresql://postgres:postgres_password@127.0.0.1:5435/postgres"
PG_DB_URL = "postgresql://postgres:postgres_password@127.0.0.1:5435/retail_commerce_pg"

MYSQL_ADMIN_URL = "mysql+pymysql://root:mysql_password@127.0.0.1:3307/"
MYSQL_DB_URL = "mysql+pymysql://root:mysql_password@127.0.0.1:3307/retail_logistics_mysql"

MONGO_URL = "mongodb://127.0.0.1:27017"
MONGO_DB_NAME = "retail_experience_mongo"


# ---------------------------------------------------------------------------
# Step 1: Database Creation
# ---------------------------------------------------------------------------
def create_databases():
    print("\n=======================================================")
    print(">>> 1. Creating Target Databases across PG, MySQL & Mongo")
    print("=======================================================")

    # PostgreSQL
    print("Connecting to PostgreSQL admin (Port 5435)...")
    pg_admin_engine = create_engine(PG_ADMIN_URL, isolation_level="AUTOCOMMIT")
    with pg_admin_engine.connect() as conn:
        res = conn.execute(text("SELECT 1 FROM pg_database WHERE datname='retail_commerce_pg'"))
        if not res.scalar():
            conn.execute(text('CREATE DATABASE "retail_commerce_pg"'))
            print("  [OK] Created PostgreSQL database 'retail_commerce_pg'.")
        else:
            print("  [OK] PostgreSQL database 'retail_commerce_pg' already exists.")

    # MySQL
    print("Connecting to MySQL admin (Port 3307)...")
    mysql_admin_engine = create_engine(MYSQL_ADMIN_URL, isolation_level="AUTOCOMMIT")
    with mysql_admin_engine.connect() as conn:
        conn.execute(text("CREATE DATABASE IF NOT EXISTS retail_logistics_mysql"))
        print("  [OK] Created/verified MySQL database 'retail_logistics_mysql'.")

    # MongoDB
    print("Connecting to MongoDB (Port 27017)...")
    mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    mongo_db = mongo_client[MONGO_DB_NAME]
    mongo_db.command("ping")
    print("  [OK] Connected to MongoDB database 'retail_experience_mongo'.")


# ---------------------------------------------------------------------------
# Step 2: Seed MySQL Logistics & Supply Chain
# ---------------------------------------------------------------------------
def seed_mysql():
    print("\n=======================================================")
    print(">>> 2. Seeding MySQL: retail_logistics_mysql (Port 3307)")
    print("=======================================================")
    engine = create_engine(MYSQL_DB_URL, isolation_level="AUTOCOMMIT")

    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        conn.execute(text("DROP TABLE IF EXISTS consignment_items;"))
        conn.execute(text("DROP TABLE IF EXISTS shipment_consignments;"))
        conn.execute(text("DROP TABLE IF EXISTS inventory_stock;"))
        conn.execute(text("DROP TABLE IF EXISTS products_catalog;"))
        conn.execute(text("DROP TABLE IF EXISTS warehouses;"))
        conn.execute(text("DROP TABLE IF EXISTS suppliers;"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))

        # 1. suppliers
        conn.execute(text("""
            CREATE TABLE suppliers (
                supplier_id INT PRIMARY KEY AUTO_INCREMENT,
                supplier_code VARCHAR(30) UNIQUE NOT NULL,
                company_name VARCHAR(150) NOT NULL,
                contact_email VARCHAR(150) NOT NULL,
                country_code VARCHAR(10) NOT NULL,
                lead_time_days INT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB;
        """))

        # 2. warehouses
        conn.execute(text("""
            CREATE TABLE warehouses (
                warehouse_id INT PRIMARY KEY AUTO_INCREMENT,
                facility_code VARCHAR(30) UNIQUE NOT NULL,
                facility_name VARCHAR(150) NOT NULL,
                city VARCHAR(100) NOT NULL,
                state VARCHAR(100),
                storage_capacity_units INT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB;
        """))

        # 3. products_catalog
        conn.execute(text("""
            CREATE TABLE products_catalog (
                product_id INT PRIMARY KEY AUTO_INCREMENT,
                sku VARCHAR(60) UNIQUE NOT NULL,
                product_name VARCHAR(200) NOT NULL,
                supplier_id INT NOT NULL,
                weight_kg DECIMAL(6, 2) NOT NULL,
                retail_price DECIMAL(10, 2) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_catalog_supplier FOREIGN KEY (supplier_id) 
                    REFERENCES suppliers(supplier_id) ON DELETE RESTRICT
            ) ENGINE=InnoDB;
        """))

        # 4. inventory_stock
        conn.execute(text("""
            CREATE TABLE inventory_stock (
                stock_id INT PRIMARY KEY AUTO_INCREMENT,
                product_id INT NOT NULL,
                warehouse_id INT NOT NULL,
                quantity_available INT NOT NULL,
                quantity_reserved INT NOT NULL DEFAULT 0,
                reorder_threshold INT NOT NULL,
                last_restocked_at DATETIME,
                CONSTRAINT fk_stock_product FOREIGN KEY (product_id) 
                    REFERENCES products_catalog(product_id) ON DELETE CASCADE,
                CONSTRAINT fk_stock_warehouse FOREIGN KEY (warehouse_id) 
                    REFERENCES warehouses(warehouse_id) ON DELETE CASCADE,
                UNIQUE KEY uq_product_warehouse (product_id, warehouse_id)
            ) ENGINE=InnoDB;
        """))

        # 5. shipment_consignments
        conn.execute(text("""
            CREATE TABLE shipment_consignments (
                consignment_id INT PRIMARY KEY AUTO_INCREMENT,
                order_id INT NOT NULL,
                dispatch_warehouse_id INT NOT NULL,
                carrier_name VARCHAR(80) NOT NULL,
                tracking_number VARCHAR(100) UNIQUE NOT NULL,
                fulfillment_status VARCHAR(50) NOT NULL,
                dispatched_at DATETIME,
                delivered_at DATETIME,
                CONSTRAINT fk_consignment_warehouse FOREIGN KEY (dispatch_warehouse_id) 
                    REFERENCES warehouses(warehouse_id) ON DELETE RESTRICT
            ) ENGINE=InnoDB;
        """))

        # 6. consignment_items
        conn.execute(text("""
            CREATE TABLE consignment_items (
                consignment_item_id INT PRIMARY KEY AUTO_INCREMENT,
                consignment_id INT NOT NULL,
                product_id INT NOT NULL,
                quantity_shipped INT NOT NULL,
                CONSTRAINT fk_item_consignment FOREIGN KEY (consignment_id) 
                    REFERENCES shipment_consignments(consignment_id) ON DELETE CASCADE,
                CONSTRAINT fk_item_product FOREIGN KEY (product_id) 
                    REFERENCES products_catalog(product_id) ON DELETE RESTRICT
            ) ENGINE=InnoDB;
        """))

    print("  [OK] MySQL tables with strict InnoDB foreign keys created.")

    # Seed Data
    suppliers_data = [
        ("SUP-01", "Apex Electronics Ltd", "procurement@apexelectronics.com", "US", 5),
        ("SUP-02", "Nordic Audio Works", "supply@nordicaudio.se", "SE", 12),
        ("SUP-03", "Precision Optics Gmbh", "sales@precisionoptics.de", "DE", 8),
        ("SUP-04", "Kyoto Silicon Systems", "b2b@kyotosilicon.jp", "JP", 14),
        ("SUP-05", "Cascade Home Goods", "orders@cascadehome.com", "US", 4),
        ("SUP-06", "Valencian Leathercraft", "export@valenciancraft.es", "ES", 10),
        ("SUP-07", "Alpine Sports Gear", "partner@alpinesports.ch", "CH", 7),
        ("SUP-08", "Seoul Smart Appliances", "global@seoulsmart.kr", "KR", 15),
    ]

    warehouses_data = [
        ("WH-EAST-01", "New Jersey Regional Hub", "Edison", "NJ", 250000),
        ("WH-WEST-01", "Nevada Logistics Center", "Reno", "NV", 320000),
        ("WH-MID-01", "Chicago Crossroads Facility", "Elwood", "IL", 180000),
        ("WH-SOUTH-01", "Dallas Gateway Terminal", "Grapevine", "TX", 220000),
    ]

    products_data = [
        ("SKU-AUDIO-001", "Pro ANC Wireless Headphones", 2, 0.45, 249.99),
        ("SKU-AUDIO-002", "Studio Hi-Fi Reference Monitors", 2, 4.20, 499.00),
        ("SKU-TECH-001", "Ultra-Wide Gaming Monitor 34\"", 1, 6.80, 799.50),
        ("SKU-TECH-002", "Mechanical Tactile Keyboard RGB", 1, 1.10, 149.00),
        ("SKU-TECH-003", "Ergonomic Precision Mouse", 1, 0.15, 89.99),
        ("SKU-OPTIC-001", "Anamorphic Cinematic Lens 50mm", 3, 0.85, 1150.00),
        ("SKU-OPTIC-002", "Variable ND Filter Set 82mm", 3, 0.20, 129.00),
        ("SKU-HOME-001", "Smart Barista Espresso Machine", 8, 8.50, 649.00),
        ("SKU-HOME-002", "Ceramic Pour-Over Kettle", 5, 0.90, 65.00),
        ("SKU-FASH-001", "Full-Grain Leather Messenger Bag", 6, 1.40, 280.00),
        ("SKU-FASH-002", "Minimalist Cardholder Wallet", 6, 0.08, 45.00),
        ("SKU-SPORT-001", "Ultralight Carbon Hiking Poles", 7, 0.48, 120.00),
        ("SKU-SPORT-002", "Waterproof Mountaineering Backpack 45L", 7, 1.60, 210.00),
        ("SKU-CHIP-001", "Embedded IoT Edge Controller", 4, 0.05, 35.00),
        ("SKU-CHIP-002", "High-Density NVMe Enclosure USB4", 4, 0.22, 95.00),
    ]

    with engine.begin() as conn:
        for code, name, email, country, lead in suppliers_data:
            conn.execute(text("""
                INSERT INTO suppliers (supplier_code, company_name, contact_email, country_code, lead_time_days)
                VALUES (:code, :name, :email, :country, :lead)
            """), {"code": code, "name": name, "email": email, "country": country, "lead": lead})

        for code, name, city, state, cap in warehouses_data:
            conn.execute(text("""
                INSERT INTO warehouses (facility_code, facility_name, city, state, storage_capacity_units)
                VALUES (:code, :name, :city, :state, :cap)
            """), {"code": code, "name": name, "city": city, "state": state, "cap": cap})

        for sku, name, sup_id, weight, price in products_data:
            conn.execute(text("""
                INSERT INTO products_catalog (sku, product_name, supplier_id, weight_kg, retail_price)
                VALUES (:sku, :name, :sup_id, :weight, :price)
            """), {"sku": sku, "name": name, "sup_id": sup_id, "weight": weight, "price": price})

        # Seed inventory stock across warehouses
        for prod_id in range(1, len(products_data) + 1):
            for wh_id in [1, 2, 3]:
                qty = random.randint(40, 500)
                res = random.randint(0, 15)
                reorder = random.randint(20, 50)
                conn.execute(text("""
                    INSERT INTO inventory_stock (product_id, warehouse_id, quantity_available, quantity_reserved, reorder_threshold, last_restocked_at)
                    VALUES (:prod_id, :wh_id, :qty, :res, :reorder, NOW() - INTERVAL :days DAY)
                """), {"prod_id": prod_id, "wh_id": wh_id, "qty": qty, "res": res, "reorder": reorder, "days": random.randint(1, 20)})

    print(f"  [OK] Seeded {len(suppliers_data)} suppliers, {len(warehouses_data)} warehouses, {len(products_data)} catalog products, and inventory stock.")
    return products_data


# ---------------------------------------------------------------------------
# Step 3: Seed PostgreSQL Commerce & Financial Transactions
# ---------------------------------------------------------------------------
def seed_postgres(products_catalog):
    print("\n=======================================================")
    print(">>> 3. Seeding PostgreSQL: retail_commerce_pg (Port 5435)")
    print("=======================================================")
    engine = create_engine(PG_DB_URL)

    with engine.begin() as conn:
        conn.execute(text("""
            DROP TABLE IF EXISTS payments CASCADE;
            DROP TABLE IF EXISTS order_items CASCADE;
            DROP TABLE IF EXISTS orders CASCADE;
            DROP TABLE IF EXISTS customer_addresses CASCADE;
            DROP TABLE IF EXISTS customers CASCADE;
        """))

        # 1. customers
        conn.execute(text("""
            CREATE TABLE customers (
                customer_id INT PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                first_name VARCHAR(100) NOT NULL,
                last_name VARCHAR(100) NOT NULL,
                loyalty_tier VARCHAR(50) NOT NULL DEFAULT 'BRONZE',
                phone VARCHAR(50),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # 2. customer_addresses
        conn.execute(text("""
            CREATE TABLE customer_addresses (
                address_id SERIAL PRIMARY KEY,
                customer_id INT NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
                street VARCHAR(255) NOT NULL,
                city VARCHAR(100) NOT NULL,
                state VARCHAR(100) NOT NULL,
                postal_code VARCHAR(20) NOT NULL,
                country VARCHAR(50) NOT NULL DEFAULT 'USA',
                is_billing_default BOOLEAN NOT NULL DEFAULT FALSE
            );
        """))

        # 3. orders
        conn.execute(text("""
            CREATE TABLE orders (
                order_id INT PRIMARY KEY,
                customer_id INT NOT NULL REFERENCES customers(customer_id) ON DELETE RESTRICT,
                order_status VARCHAR(50) NOT NULL,
                order_date TIMESTAMP WITH TIME ZONE NOT NULL,
                shipping_address_id INT REFERENCES customer_addresses(address_id),
                subtotal NUMERIC(10, 2) NOT NULL,
                tax_amount NUMERIC(10, 2) NOT NULL,
                total_amount NUMERIC(10, 2) NOT NULL
            );
        """))

        # 4. order_items (product_sku cross-ref to MySQL products_catalog.sku)
        conn.execute(text("""
            CREATE TABLE order_items (
                order_item_id SERIAL PRIMARY KEY,
                order_id INT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
                product_sku VARCHAR(60) NOT NULL,
                quantity INT NOT NULL,
                unit_price NUMERIC(10, 2) NOT NULL,
                discount_amount NUMERIC(10, 2) NOT NULL DEFAULT 0.00
            );
        """))

        # 5. payments
        conn.execute(text("""
            CREATE TABLE payments (
                payment_id SERIAL PRIMARY KEY,
                order_id INT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
                payment_method VARCHAR(50) NOT NULL,
                payment_status VARCHAR(50) NOT NULL,
                amount NUMERIC(10, 2) NOT NULL,
                transaction_ref VARCHAR(100) UNIQUE NOT NULL,
                processed_at TIMESTAMP WITH TIME ZONE NOT NULL
            );
        """))

    print("  [OK] PostgreSQL tables with strict foreign keys created.")

    # 25 Customers
    customers_data = [
        (1001, "oliver.smith@domain.com", "Oliver", "Smith", "PLATINUM", "+1-555-0101"),
        (1002, "emma.johnson@domain.com", "Emma", "Johnson", "GOLD", "+1-555-0102"),
        (1003, "liam.williams@domain.com", "Liam", "Williams", "SILVER", "+1-555-0103"),
        (1004, "sophia.brown@domain.com", "Sophia", "Brown", "PLATINUM", "+1-555-0104"),
        (1005, "noah.jones@domain.com", "Noah", "Jones", "BRONZE", "+1-555-0105"),
        (1006, "ava.garcia@domain.com", "Ava", "Garcia", "GOLD", "+1-555-0106"),
        (1007, "lucas.miller@domain.com", "Lucas", "Miller", "SILVER", "+1-555-0107"),
        (1008, "mia.davis@domain.com", "Mia", "Davis", "GOLD", "+1-555-0108"),
        (1009, "ethan.rodriguez@domain.com", "Ethan", "Rodriguez", "BRONZE", "+1-555-0109"),
        (1010, "isabella.martinez@domain.com", "Isabella", "Martinez", "PLATINUM", "+1-555-0110"),
        (1011, "mason.hernandez@domain.com", "Mason", "Hernandez", "SILVER", "+1-555-0111"),
        (1012, "charlotte.lopez@domain.com", "Charlotte", "Lopez", "GOLD", "+1-555-0112"),
        (1013, "logan.gonzalez@domain.com", "Logan", "Gonzalez", "BRONZE", "+1-555-0113"),
        (1014, "amelia.wilson@domain.com", "Amelia", "Wilson", "PLATINUM", "+1-555-0114"),
        (1015, "james.anderson@domain.com", "James", "Anderson", "SILVER", "+1-555-0115"),
        (1016, "harper.thomas@domain.com", "Harper", "Thomas", "GOLD", "+1-555-0116"),
        (1017, "benjamin.taylor@domain.com", "Benjamin", "Taylor", "BRONZE", "+1-555-0117"),
        (1018, "evelyn.moore@domain.com", "Evelyn", "Moore", "PLATINUM", "+1-555-0118"),
        (1019, "elijah.jackson@domain.com", "Elijah", "Jackson", "SILVER", "+1-555-0119"),
        (1020, "abigail.martin@domain.com", "Abigail", "Martin", "GOLD", "+1-555-0120"),
        (1021, "alexander.lee@domain.com", "Alexander", "Lee", "BRONZE", "+1-555-0121"),
        (1022, "emily.perez@domain.com", "Emily", "Perez", "GOLD", "+1-555-0122"),
        (1023, "daniel.thompson@domain.com", "Daniel", "Thompson", "SILVER", "+1-555-0123"),
        (1024, "elizabeth.white@domain.com", "Elizabeth", "White", "PLATINUM", "+1-555-0124"),
        (1025, "henry.harris@domain.com", "Henry", "Harris", "BRONZE", "+1-555-0125"),
    ]

    addresses_sample = [
        ("742 Evergreen Terrace", "Springfield", "OR", "97477"),
        ("100 Pine Crest Way", "Austin", "TX", "78701"),
        ("520 Market Street", "San Francisco", "CA", "94105"),
        ("12 Elmwood Avenue", "Seattle", "WA", "98101"),
        ("888 Michigan Ave", "Chicago", "IL", "60611"),
    ]

    orders_created = []

    with engine.begin() as conn:
        # Insert customers and addresses
        addr_id_map = {}
        for cid, email, fn, ln, tier, phone in customers_data:
            conn.execute(text("""
                INSERT INTO customers (customer_id, email, first_name, last_name, loyalty_tier, phone)
                VALUES (:cid, :email, :fn, :ln, :tier, :phone)
            """), {"cid": cid, "email": email, "fn": fn, "ln": ln, "tier": tier, "phone": phone})

            street, city, state, zip_code = addresses_sample[cid % len(addresses_sample)]
            res = conn.execute(text("""
                INSERT INTO customer_addresses (customer_id, street, city, state, postal_code, is_billing_default)
                VALUES (:cid, :street, :city, :state, :zip_code, TRUE)
                RETURNING address_id
            """), {"cid": cid, "street": street, "city": city, "state": state, "zip_code": zip_code})
            addr_id_map[cid] = res.scalar()

        # Generate 35 Orders with items and payments
        base_time = datetime.now(timezone.utc) - timedelta(days=60)
        sku_lookup = {item[0]: Decimal(str(item[4])) for item in products_catalog}
        sku_list = list(sku_lookup.keys())

        for order_idx in range(1, 36):
            order_id = 5000 + order_idx
            cid = 1001 + (order_idx % len(customers_data))
            addr_id = addr_id_map[cid]
            order_date = base_time + timedelta(days=order_idx, hours=random.randint(1, 12))
            status = random.choice(["COMPLETED", "COMPLETED", "COMPLETED", "PROCESSING", "SHIPPED"])

            # Pick 1 to 3 items
            selected_skus = random.sample(sku_list, k=random.randint(1, 3))
            subtotal = Decimal("0.00")
            items_to_insert = []

            for sku in selected_skus:
                qty = random.randint(1, 2)
                price = sku_lookup[sku]
                subtotal += price * qty
                items_to_insert.append((sku, qty, price))

            tax = (subtotal * Decimal("0.0825")).quantize(Decimal("0.01"))
            total = subtotal + tax

            conn.execute(text("""
                INSERT INTO orders (order_id, customer_id, order_status, order_date, shipping_address_id, subtotal, tax_amount, total_amount)
                VALUES (:oid, :cid, :status, :odate, :aid, :subtotal, :tax, :total)
            """), {"oid": order_id, "cid": cid, "status": status, "odate": order_date, "aid": addr_id, "subtotal": subtotal, "tax": tax, "total": total})

            for sku, qty, price in items_to_insert:
                conn.execute(text("""
                    INSERT INTO order_items (order_id, product_sku, quantity, unit_price)
                    VALUES (:oid, :sku, :qty, :price)
                """), {"oid": order_id, "sku": sku, "qty": qty, "price": price})

            # Payment
            conn.execute(text("""
                INSERT INTO payments (order_id, payment_method, payment_status, amount, transaction_ref, processed_at)
                VALUES (:oid, :method, :pstatus, :amount, :tx_ref, :pdate)
            """), {
                "oid": order_id,
                "method": random.choice(["CREDIT_CARD", "APPLE_PAY", "PAYPAL", "STRIPE"]),
                "pstatus": "SETTLED" if status in ["COMPLETED", "SHIPPED"] else "AUTHORIZED",
                "amount": total,
                "tx_ref": f"TXN-{order_id}-{random.randint(100000, 999999)}",
                "pdate": order_date + timedelta(minutes=random.randint(1, 10))
            })

            orders_created.append((order_id, cid, status, order_date, items_to_insert))

    print(f"  [OK] Seeded {len(customers_data)} customers, {len(orders_created)} orders, order items, and payments.")
    return customers_data, orders_created


# ---------------------------------------------------------------------------
# Step 4: Link MySQL Shipments to PG Orders (Intra-Connection)
# ---------------------------------------------------------------------------
def link_mysql_shipments(orders_created):
    print("\n=======================================================")
    print(">>> 4. Linking MySQL Consignments to PostgreSQL Orders")
    print("=======================================================")
    engine = create_engine(MYSQL_DB_URL, isolation_level="AUTOCOMMIT")

    # Fetch product_id map from MySQL
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT product_id, sku FROM products_catalog")).fetchall()
        sku_to_id = {row[1]: row[0] for row in rows}

    carriers = ["FedEx Express", "UPS Ground", "DHL eCommerce", "USPS Priority"]
    shipped_count = 0

    with engine.begin() as conn:
        for order_id, cid, status, order_date, items in orders_created:
            if status in ["COMPLETED", "SHIPPED"]:
                wh_id = random.choice([1, 2, 3, 4])
                carrier = random.choice(carriers)
                tracking = f"TRK-{order_id}-{random.randint(100000, 999999)}"
                f_status = "DELIVERED" if status == "COMPLETED" else "IN_TRANSIT"
                disp_date = order_date + timedelta(days=1)
                deliv_date = disp_date + timedelta(days=2) if f_status == "DELIVERED" else None

                res = conn.execute(text("""
                    INSERT INTO shipment_consignments (order_id, dispatch_warehouse_id, carrier_name, tracking_number, fulfillment_status, dispatched_at, delivered_at)
                    VALUES (:oid, :wh_id, :carrier, :tracking, :f_status, :disp, :deliv)
                """), {
                    "oid": order_id,
                    "wh_id": wh_id,
                    "carrier": carrier,
                    "tracking": tracking,
                    "f_status": f_status,
                    "disp": disp_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "deliv": deliv_date.strftime("%Y-%m-%d %H:%M:%S") if deliv_date else None
                })
                consignment_id = res.lastrowid

                for sku, qty, _ in items:
                    if sku in sku_to_id:
                        conn.execute(text("""
                            INSERT INTO consignment_items (consignment_id, product_id, quantity_shipped)
                            VALUES (:cid, :pid, :qty)
                        """), {"cid": consignment_id, "pid": sku_to_id[sku], "qty": qty})

                shipped_count += 1

    print(f"  [OK] Successfully linked {shipped_count} MySQL consignments & consignment items to PG order_ids.")


# ---------------------------------------------------------------------------
# Step 5: Seed MongoDB Experience, Telemetry & Helpdesk (Intra-Connection)
# ---------------------------------------------------------------------------
def seed_mongo(customers_data, orders_created, products_catalog):
    print("\n=======================================================")
    print(">>> 5. Seeding MongoDB: retail_experience_mongo (Port 27017)")
    print("=======================================================")
    mongo_client = MongoClient(MONGO_URL)
    db = mongo_client[MONGO_DB_NAME]

    # Clean existing collections
    for col in ["customer_profiles", "session_events", "support_tickets", "product_reviews"]:
        db[col].drop()

    # 1. customer_profiles (cross-ref: customer_id)
    profile_docs = []
    tiers_points = {"PLATINUM": 4500, "GOLD": 2200, "SILVER": 850, "BRONZE": 120}
    for cid, email, fn, ln, tier, phone in customers_data:
        profile_docs.append({
            "customer_id": cid,
            "email": email,
            "full_name": f"{fn} {ln}",
            "loyalty_ledger": {
                "tier": tier,
                "lifetime_points": tiers_points.get(tier, 100),
                "redeemable_points": tiers_points.get(tier, 100) // 2,
                "membership_anniversary": (datetime.now(timezone.utc) - timedelta(days=random.randint(100, 500))).isoformat()
            },
            "preferences": {
                "newsletter_subscribed": random.choice([True, False]),
                "preferred_contact_method": random.choice(["email", "sms"]),
                "categories_of_interest": random.sample(["Audio", "Electronics", "Fashion", "Outdoor", "Home Goods"], k=2)
            },
            "registered_devices": [
                {"device_type": "mobile", "os": "iOS 17.4", "push_token": f"apns_{cid}_{random.randint(1000, 9999)}"},
                {"device_type": "desktop", "os": "macOS Sonoma", "browser": "Chrome 124"}
            ],
            "last_login": (datetime.now(timezone.utc) - timedelta(days=random.randint(1, 10))).isoformat()
        })
    db.customer_profiles.insert_many(profile_docs)
    print(f"  [OK] Seeded {len(profile_docs)} MongoDB customer_profiles referencing PG customers.")

    # 2. product_reviews (cross-ref: product_sku to MySQL, customer_id to PG)
    review_docs = []
    reviews_sample = [
        ("Exceptional build quality!", "Exceeded my expectations in every way. The finishes are flawless.", 5, 0.94),
        ("Great performance for the price", "Works seamlessly in my daily workflow. Battery life could be slightly longer.", 4, 0.78),
        ("Good, but initial setup took time", "Overall satisfied once configured properly, high premium feel.", 4, 0.65),
        ("Best purchase this year", "Highly recommend this to anyone looking for professional grade equipment.", 5, 0.98),
        ("Decent product", "Average quality, works as advertised.", 3, 0.20),
    ]

    for sku_tuple in products_catalog[:10]:
        sku = sku_tuple[0]
        # Pick 2-3 reviews per product from random customers
        selected_customers = random.sample(customers_data, k=random.randint(2, 3))
        for cust in selected_customers:
            cid = cust[0]
            title, body, rating, sentiment = random.choice(reviews_sample)
            review_docs.append({
                "product_sku": sku,
                "customer_id": cid,
                "customer_name": f"{cust[2]} {cust[3][:1]}.",
                "rating": rating,
                "review_title": title,
                "review_body": body,
                "sentiment_score": sentiment,
                "verified_purchase": True,
                "helpful_votes": random.randint(1, 35),
                "created_at": (datetime.now(timezone.utc) - timedelta(days=random.randint(5, 45))).isoformat()
            })
    db.product_reviews.insert_many(review_docs)
    print(f"  [OK] Seeded {len(review_docs)} MongoDB product_reviews referencing MySQL SKUs & PG customers.")

    # 3. support_tickets (cross-ref: customer_id to PG, related_order_id to PG)
    ticket_docs = []
    ticket_scenarios = [
        ("Delivery Inquiry", "Where is my shipment currently located?", "Shipment is currently out for delivery today with FedEx.", "SHIPPING"),
        ("Invoice Request", "Could you please send me a VAT invoice for this order?", "Invoice PDF attached and sent to your primary email address.", "BILLING"),
        ("Compatibility Question", "Does this accessory fit with my current setup?", "Yes, it supports USB4 and Thunderbolt interfaces seamlessly.", "PRODUCT_SUPPORT"),
    ]

    for order_id, cid, status, order_date, items in orders_created[:12]:
        scenario = random.choice(ticket_scenarios)
        ticket_docs.append({
            "ticket_id": f"TCK-{order_id}-{random.randint(100, 999)}",
            "customer_id": cid,
            "related_order_id": order_id,
            "issue_category": scenario[3],
            "priority": random.choice(["MEDIUM", "HIGH", "LOW"]),
            "status": "RESOLVED" if status == "COMPLETED" else "IN_PROGRESS",
            "messages": [
                {
                    "sender_type": "customer",
                    "sender_id": str(cid),
                    "timestamp": (order_date + timedelta(days=1, hours=2)).isoformat(),
                    "body": scenario[1]
                },
                {
                    "sender_type": "agent",
                    "sender_id": "support_agent_04",
                    "timestamp": (order_date + timedelta(days=1, hours=3)).isoformat(),
                    "body": scenario[2]
                }
            ],
            "resolution_satisfaction_rating": random.choice([4, 5]) if status == "COMPLETED" else None
        })
    db.support_tickets.insert_many(ticket_docs)
    print(f"  [OK] Seeded {len(ticket_docs)} MongoDB support_tickets referencing PG orders and customers.")

    # 4. session_events (cross-ref: customer_id to PG, viewed_skus to MySQL)
    session_docs = []
    all_skus = [p[0] for p in products_catalog]
    for cid, _, _, _, _, _ in customers_data[:15]:
        session_id = f"sess_{cid}_{random.randint(100000, 999999)}"
        viewed = random.sample(all_skus, k=random.randint(2, 4))
        session_docs.append({
            "session_id": session_id,
            "customer_id": cid,
            "ip_address": f"192.168.1.{random.randint(10, 240)}",
            "device": {
                "browser": "Chrome",
                "os": "macOS",
                "is_mobile": False
            },
            "viewed_skus": viewed,
            "page_path_events": [
                {"path": "/", "dwell_time_sec": 12},
                {"path": f"/catalog/{viewed[0]}", "dwell_time_sec": 45},
                {"path": f"/catalog/{viewed[1]}", "dwell_time_sec": 30},
                {"path": "/checkout", "dwell_time_sec": 90}
            ],
            "cart_additions": [
                {"sku": viewed[0], "quantity": 1}
            ],
            "started_at": (datetime.now(timezone.utc) - timedelta(days=random.randint(1, 15))).isoformat()
        })
    db.session_events.insert_many(session_docs)
    print(f"  [OK] Seeded {len(session_docs)} MongoDB session_events referencing PG customers & MySQL SKUs.")


def sync_mongo_to_docker():
    import subprocess
    import json
    from bson import json_util
    print("\n=======================================================")
    print(">>> 6. Syncing MongoDB to Docker: migration_platform_mongo")
    print("=======================================================")
    try:
        mongo_client = MongoClient(MONGO_URL)
        db = mongo_client[MONGO_DB_NAME]
        for col_name in ["customer_profiles", "session_events", "support_tickets", "product_reviews"]:
            docs = list(db[col_name].find())
            if not docs:
                continue
            clean_docs = []
            for d in docs:
                dc = dict(d)
                dc.pop('_id', None)
                clean_docs.append(dc)
            json_docs = json.dumps(clean_docs, default=str)
            js_script = f"db = db.getSiblingDB('{MONGO_DB_NAME}'); db['{col_name}'].drop(); db['{col_name}'].insertMany({json_docs});\n"
            proc = subprocess.run(
                ["docker", "exec", "-i", "migration_platform_mongo", "mongosh", "-u", "root", "-p", "mongo_password", "--quiet"],
                input=js_script, capture_output=True, text=True, timeout=15
            )
            if proc.returncode == 0:
                print(f"  [OK] Synced {len(docs)} documents for '{col_name}' into Docker MongoDB.")
            else:
                print(f"  [Notice] Docker Mongo sync for '{col_name}': {proc.stderr.strip()[:100]}")
    except Exception as e:
        print(f"  [Notice] Could not sync to Docker MongoDB: {e}")


# ---------------------------------------------------------------------------
# Main Execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=================================================================")
    print("STARTING POLYGLOT ENTERPRISE DATABASE INITIALIZATION & SEEDING")
    print("=================================================================")
    start_time = time.time()

    create_databases()
    products_catalog = seed_mysql()
    customers_data, orders_created = seed_postgres(products_catalog)
    link_mysql_shipments(orders_created)
    seed_mongo(customers_data, orders_created, products_catalog)
    sync_mongo_to_docker()

    duration = round(time.time() - start_time, 2)
    print("\n=================================================================")
    print(f"POLYGLOT ENTERPRISE SEEDING COMPLETE IN {duration}s!")
    print("=================================================================")

