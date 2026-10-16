CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE IF NOT EXISTS customer(
  customer_id SERIAL PRIMARY KEY, full_name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  phone TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS service_category(
  category_id SERIAL PRIMARY KEY, category_name TEXT NOT NULL UNIQUE, description TEXT);
CREATE TABLE IF NOT EXISTS service(
  service_id SERIAL PRIMARY KEY,
  category_id INT NOT NULL REFERENCES service_category, service_name TEXT NOT NULL,
  description TEXT, duration_minutes INT NOT NULL CHECK (duration_minutes>0),
  base_price NUMERIC(10,2) NOT NULL CHECK (base_price>=0), active BOOLEAN NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS staff(
  staff_id SERIAL PRIMARY KEY, full_name TEXT NOT NULL, email TEXT UNIQUE, phone TEXT,
  role TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS staff_skill(
  staff_id INT REFERENCES staff ON DELETE CASCADE, service_id INT REFERENCES service,
  skill_level INT NOT NULL CHECK (skill_level BETWEEN 1 AND 5),
  experience_years NUMERIC(4,1) NOT NULL DEFAULT 0 CHECK (experience_years>=0),
  PRIMARY KEY (staff_id, service_id));
CREATE TABLE IF NOT EXISTS staff_schedule(
  schedule_id SERIAL PRIMARY KEY, staff_id INT NOT NULL REFERENCES staff ON DELETE CASCADE,
  working_date DATE NOT NULL, start_time TIME NOT NULL, end_time TIME NOT NULL,
  available BOOLEAN NOT NULL DEFAULT true,
  CHECK (end_time>start_time), UNIQUE (staff_id, working_date));
CREATE TABLE IF NOT EXISTS appointment(
  appointment_id SERIAL PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customer, staff_id INT NOT NULL REFERENCES staff,
  appointment_datetime TIMESTAMP NOT NULL, end_datetime TIMESTAMP NOT NULL,
  status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','completed','cancelled')),
  booked_at TIMESTAMPTZ NOT NULL DEFAULT now(), notes TEXT,
  CHECK (end_datetime>appointment_datetime),
  EXCLUDE USING gist (staff_id WITH =, tsrange(appointment_datetime,end_datetime) WITH &&)
    WHERE (status<>'cancelled'));
CREATE TABLE IF NOT EXISTS appointment_service(
  appointment_id INT REFERENCES appointment ON DELETE CASCADE, service_id INT REFERENCES service,
  agreed_price NUMERIC(10,2) NOT NULL CHECK (agreed_price>=0),
  duration_minutes INT NOT NULL CHECK (duration_minutes>0),
  PRIMARY KEY (appointment_id, service_id));
CREATE TABLE IF NOT EXISTS product(
  product_id SERIAL PRIMARY KEY, product_name TEXT NOT NULL, category TEXT,
  unit_price NUMERIC(10,2) NOT NULL CHECK (unit_price>=0),
  stock_quantity INT NOT NULL CHECK (stock_quantity>=0), reorder_level INT NOT NULL DEFAULT 5);
CREATE TABLE IF NOT EXISTS bill(
  bill_id SERIAL PRIMARY KEY, appointment_id INT NOT NULL UNIQUE REFERENCES appointment,
  subtotal NUMERIC(10,2) NOT NULL, discount_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
  tax_amount NUMERIC(10,2) NOT NULL DEFAULT 0, total_amount NUMERIC(10,2) NOT NULL CHECK (total_amount>=0),
  payment_status TEXT NOT NULL DEFAULT 'unpaid' CHECK (payment_status IN ('unpaid','partial','paid')),
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS payment(
  payment_id SERIAL PRIMARY KEY, bill_id INT NOT NULL REFERENCES bill,
  amount NUMERIC(10,2) NOT NULL CHECK (amount>0),
  payment_method TEXT NOT NULL CHECK (payment_method IN ('upi','card','cash')),
  transaction_reference TEXT, paid_at TIMESTAMPTZ NOT NULL DEFAULT now());

-- Rule: appointment must be in the future (on insert), inside the staff schedule,
-- and follow status transitions booked -> completed|cancelled (terminal after).
CREATE OR REPLACE FUNCTION trg_appt_validate() RETURNS trigger AS $$
BEGIN
  IF TG_OP='INSERT' AND NEW.appointment_datetime < now() THEN
    RAISE EXCEPTION 'Appointments cannot be booked in the past'; END IF;
  IF TG_OP='UPDATE' AND OLD.status<>NEW.status AND OLD.status<>'booked' THEN
    RAISE EXCEPTION 'Invalid status change: % -> %', OLD.status, NEW.status; END IF;
  IF NEW.status='booked' AND NOT EXISTS (
    SELECT 1 FROM staff_schedule s WHERE s.staff_id=NEW.staff_id AND s.available
      AND s.working_date=NEW.appointment_datetime::date
      AND s.start_time<=NEW.appointment_datetime::time
      AND s.end_time>=NEW.end_datetime::time AND NEW.end_datetime::date=NEW.appointment_datetime::date)
  THEN RAISE EXCEPTION 'Appointment falls outside the staff member''s working hours'; END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS appt_validate ON appointment;
CREATE TRIGGER appt_validate BEFORE INSERT OR UPDATE ON appointment
  FOR EACH ROW EXECUTE FUNCTION trg_appt_validate();

-- Rule: assigned staff must have the skill for each service.
CREATE OR REPLACE FUNCTION trg_appt_skill() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM appointment a JOIN staff_skill k ON k.staff_id=a.staff_id
      WHERE a.appointment_id=NEW.appointment_id AND k.service_id=NEW.service_id) THEN
    RAISE EXCEPTION 'Selected staff member is not qualified for service %', NEW.service_id; END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS appt_skill ON appointment_service;
CREATE TRIGGER appt_skill BEFORE INSERT ON appointment_service
  FOR EACH ROW EXECUTE FUNCTION trg_appt_skill();

-- Rule: payments cannot exceed outstanding balance; bill status follows payments.
CREATE OR REPLACE FUNCTION trg_payment_check() RETURNS trigger AS $$
DECLARE tot NUMERIC; paid NUMERIC;
BEGIN
  SELECT total_amount INTO tot FROM bill WHERE bill_id=NEW.bill_id FOR UPDATE;
  SELECT COALESCE(SUM(amount),0) INTO paid FROM payment WHERE bill_id=NEW.bill_id;
  IF paid+NEW.amount>tot THEN
    RAISE EXCEPTION 'Payment exceeds outstanding balance (%)', tot-paid; END IF;
  UPDATE bill SET payment_status=CASE WHEN paid+NEW.amount=tot THEN 'paid' ELSE 'partial' END
    WHERE bill_id=NEW.bill_id;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS payment_check ON payment;
CREATE TRIGGER payment_check BEFORE INSERT ON payment
  FOR EACH ROW EXECUTE FUNCTION trg_payment_check();

CREATE OR REPLACE VIEW v_daily_revenue AS
  SELECT paid_at::date AS day, SUM(amount) AS revenue, COUNT(*) AS payments
  FROM payment GROUP BY 1;
CREATE OR REPLACE VIEW v_service_popularity AS
  SELECT s.service_name, COUNT(*) AS bookings, SUM(x.agreed_price) AS revenue
  FROM appointment_service x JOIN service s USING(service_id)
  JOIN appointment a USING(appointment_id) WHERE a.status<>'cancelled' GROUP BY 1;

-- ===== Part 2: remaining tables =====
CREATE TABLE IF NOT EXISTS staff_break(
  break_id SERIAL PRIMARY KEY, staff_id INT NOT NULL REFERENCES staff ON DELETE CASCADE,
  break_date DATE NOT NULL, start_time TIME NOT NULL, end_time TIME NOT NULL, reason TEXT,
  CHECK (end_time>start_time));
CREATE TABLE IF NOT EXISTS package(
  package_id SERIAL PRIMARY KEY, package_name TEXT NOT NULL UNIQUE, description TEXT,
  package_price NUMERIC(10,2) NOT NULL CHECK (package_price>=0),
  validity_days INT NOT NULL CHECK (validity_days>0), active BOOLEAN NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS package_service(
  package_id INT REFERENCES package ON DELETE CASCADE, service_id INT REFERENCES service,
  sessions_included INT NOT NULL CHECK (sessions_included>0), PRIMARY KEY (package_id, service_id));
CREATE TABLE IF NOT EXISTS membership(
  membership_id SERIAL PRIMARY KEY, customer_id INT NOT NULL REFERENCES customer,
  membership_name TEXT NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL,
  discount_percent NUMERIC(5,2) NOT NULL CHECK (discount_percent BETWEEN 0 AND 100),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','cancelled')),
  CHECK (end_date>=start_date));
CREATE TABLE IF NOT EXISTS package_purchase(
  purchase_id SERIAL PRIMARY KEY, customer_id INT NOT NULL REFERENCES customer,
  package_id INT NOT NULL REFERENCES package, purchase_date DATE NOT NULL DEFAULT current_date,
  expiry_date DATE NOT NULL, sessions_remaining INT NOT NULL CHECK (sessions_remaining>=0),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','exhausted','expired')));
CREATE TABLE IF NOT EXISTS package_redemption(
  redemption_id SERIAL PRIMARY KEY, purchase_id INT NOT NULL REFERENCES package_purchase,
  appointment_id INT NOT NULL REFERENCES appointment,
  sessions_used INT NOT NULL CHECK (sessions_used>0), redeemed_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS product_usage(
  usage_id SERIAL PRIMARY KEY, appointment_id INT NOT NULL REFERENCES appointment,
  product_id INT NOT NULL REFERENCES product, quantity_used INT NOT NULL CHECK (quantity_used>0));
CREATE TABLE IF NOT EXISTS appointment_cancellation(
  cancellation_id SERIAL PRIMARY KEY, appointment_id INT NOT NULL UNIQUE REFERENCES appointment,
  cancelled_at TIMESTAMPTZ NOT NULL DEFAULT now(), cancelled_by TEXT NOT NULL DEFAULT 'customer',
  reason TEXT, refund_amount NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (refund_amount>=0));
-- Extra table (documented): audit trail of appointment and payment changes.
CREATE TABLE IF NOT EXISTS audit_log(
  audit_id SERIAL PRIMARY KEY, table_name TEXT NOT NULL, operation TEXT NOT NULL,
  row_data JSONB, changed_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE INDEX IF NOT EXISTS ix_appt_customer ON appointment(customer_id);
CREATE INDEX IF NOT EXISTS ix_appt_start ON appointment(appointment_datetime);
CREATE INDEX IF NOT EXISTS ix_payment_bill ON payment(bill_id);
CREATE INDEX IF NOT EXISTS ix_usage_product ON product_usage(product_id);
CREATE INDEX IF NOT EXISTS ix_purchase_customer ON package_purchase(customer_id);

-- Staff breaks block bookings.
CREATE OR REPLACE FUNCTION trg_appt_break() RETURNS trigger AS $$
BEGIN
  IF NEW.status='booked' AND EXISTS (SELECT 1 FROM staff_break b WHERE b.staff_id=NEW.staff_id
     AND tsrange(b.break_date+b.start_time, b.break_date+b.end_time) && tsrange(NEW.appointment_datetime,NEW.end_datetime))
  THEN RAISE EXCEPTION 'Appointment overlaps a staff break'; END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS appt_break ON appointment;
CREATE TRIGGER appt_break BEFORE INSERT OR UPDATE ON appointment FOR EACH ROW EXECUTE FUNCTION trg_appt_break();

-- Stock validation + deduction.
CREATE OR REPLACE FUNCTION trg_usage_stock() RETURNS trigger AS $$
BEGIN
  UPDATE product SET stock_quantity=stock_quantity-NEW.quantity_used
   WHERE product_id=NEW.product_id AND stock_quantity>=NEW.quantity_used;
  IF NOT FOUND THEN RAISE EXCEPTION 'Insufficient stock for product %', NEW.product_id; END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS usage_stock ON product_usage;
CREATE TRIGGER usage_stock BEFORE INSERT ON product_usage FOR EACH ROW EXECUTE FUNCTION trg_usage_stock();

-- Package redemption: owner, validity, remaining sessions; decrements balance.
CREATE OR REPLACE FUNCTION trg_redeem() RETURNS trigger AS $$
DECLARE p package_purchase%ROWTYPE;
BEGIN
  SELECT * INTO p FROM package_purchase WHERE purchase_id=NEW.purchase_id FOR UPDATE;
  IF p.status<>'active' OR p.expiry_date<current_date THEN RAISE EXCEPTION 'Package is not active or has expired'; END IF;
  IF p.sessions_remaining<NEW.sessions_used THEN
    RAISE EXCEPTION 'Redemption exceeds remaining sessions (%)', p.sessions_remaining; END IF;
  IF NOT EXISTS (SELECT 1 FROM appointment a WHERE a.appointment_id=NEW.appointment_id AND a.customer_id=p.customer_id)
  THEN RAISE EXCEPTION 'Appointment does not belong to the package owner'; END IF;
  UPDATE package_purchase SET sessions_remaining=sessions_remaining-NEW.sessions_used,
    status=CASE WHEN sessions_remaining-NEW.sessions_used=0 THEN 'exhausted' ELSE status END
   WHERE purchase_id=NEW.purchase_id;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS redeem ON package_redemption;
CREATE TRIGGER redeem BEFORE INSERT ON package_redemption FOR EACH ROW EXECUTE FUNCTION trg_redeem();

-- Cancellation record + refund of amounts already paid (slot release is via the EXCLUDE WHERE clause).
CREATE OR REPLACE FUNCTION trg_cancel_rec() RETURNS trigger AS $$
BEGIN
  INSERT INTO appointment_cancellation(appointment_id,reason,refund_amount)
  VALUES (NEW.appointment_id, NULLIF(current_setting('app.reason',true),''),
    COALESCE((SELECT SUM(p.amount) FROM payment p JOIN bill b USING(bill_id) WHERE b.appointment_id=NEW.appointment_id),0))
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS cancel_rec ON appointment;
CREATE TRIGGER cancel_rec AFTER UPDATE OF status ON appointment FOR EACH ROW
  WHEN (NEW.status='cancelled' AND OLD.status<>'cancelled') EXECUTE FUNCTION trg_cancel_rec();

CREATE OR REPLACE FUNCTION trg_audit() RETURNS trigger AS $$
BEGIN INSERT INTO audit_log(table_name,operation,row_data) VALUES (TG_TABLE_NAME,TG_OP,to_jsonb(NEW)); RETURN NEW; END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_appt ON appointment;
CREATE TRIGGER audit_appt AFTER INSERT OR UPDATE ON appointment FOR EACH ROW EXECUTE FUNCTION trg_audit();
DROP TRIGGER IF EXISTS audit_pay ON payment;
CREATE TRIGGER audit_pay AFTER INSERT ON payment FOR EACH ROW EXECUTE FUNCTION trg_audit();

CREATE OR REPLACE VIEW v_upcoming_appointments AS
  SELECT a.appointment_id,a.appointment_datetime,c.full_name AS customer,s.full_name AS staff,a.status
  FROM appointment a JOIN customer c USING(customer_id) JOIN staff s USING(staff_id)
  WHERE a.status='booked' AND a.appointment_datetime>=now();
CREATE OR REPLACE VIEW v_staff_utilization AS
  SELECT s.full_name,COUNT(a.appointment_id) AS appointments,
         COALESCE(SUM(EXTRACT(EPOCH FROM a.end_datetime-a.appointment_datetime)/60),0) AS booked_minutes
  FROM staff s LEFT JOIN appointment a ON a.staff_id=s.staff_id AND a.status<>'cancelled' GROUP BY s.full_name;
CREATE OR REPLACE VIEW v_customer_visit_history AS
  SELECT c.full_name,COUNT(a.appointment_id) FILTER (WHERE a.status='completed') AS visits,MAX(a.appointment_datetime) AS last_visit
  FROM customer c LEFT JOIN appointment a USING(customer_id) GROUP BY c.full_name;
CREATE OR REPLACE VIEW v_product_inventory AS
  SELECT product_name,stock_quantity,reorder_level,stock_quantity<=reorder_level AS low_stock FROM product;
CREATE OR REPLACE VIEW v_package_session_balance AS
  SELECT pu.purchase_id,c.full_name AS customer,p.package_name,pu.sessions_remaining,pu.expiry_date,pu.status
  FROM package_purchase pu JOIN customer c USING(customer_id) JOIN package p USING(package_id);
CREATE OR REPLACE VIEW v_payment_summary AS
  SELECT b.bill_id,b.total_amount,COALESCE(SUM(p.amount),0) AS paid,b.total_amount-COALESCE(SUM(p.amount),0) AS outstanding,b.payment_status
  FROM bill b LEFT JOIN payment p USING(bill_id) GROUP BY b.bill_id;
