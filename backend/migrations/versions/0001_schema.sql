
CREATE TABLE global_patients (
	id UUID NOT NULL, 
	first_received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	latest_received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
)

;
CREATE INDEX ix_global_patients_latest_received_at ON global_patients (latest_received_at);

CREATE TABLE hospitals (
	id VARCHAR(120) NOT NULL, 
	credential_hash VARCHAR(64) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (credential_hash)
)

;

CREATE TABLE hospital_records (
	id UUID NOT NULL, 
	site_id VARCHAR(120) NOT NULL, 
	local_patient_id VARCHAR(200) NOT NULL, 
	first_received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	latest_received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	content_changed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	latest_version_id UUID, 
	matching_version_id UUID, 
	status VARCHAR(40) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (site_id, local_patient_id), 
	FOREIGN KEY(site_id) REFERENCES hospitals (id)
)

;

CREATE TABLE submissions (
	sequence BIGSERIAL NOT NULL, 
	id UUID NOT NULL, 
	site_id VARCHAR(120) NOT NULL, 
	idempotency_key VARCHAR(120) NOT NULL, 
	digest VARCHAR(64) NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(30) NOT NULL, 
	row_count INTEGER NOT NULL, 
	duplicate_count INTEGER NOT NULL, 
	payload JSONB NOT NULL, 
	results JSONB NOT NULL, 
	error TEXT, 
	PRIMARY KEY (sequence), 
	UNIQUE (site_id, idempotency_key), 
	UNIQUE (id), 
	FOREIGN KEY(site_id) REFERENCES hospitals (id)
)

;
CREATE INDEX ix_submissions_status ON submissions (status);
CREATE INDEX ix_submissions_site_id ON submissions (site_id);

CREATE TABLE audit_events (
	id BIGSERIAL NOT NULL, 
	submission_id UUID NOT NULL, 
	record_id UUID, 
	event VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	details JSONB NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(submission_id) REFERENCES submissions (id), 
	FOREIGN KEY(record_id) REFERENCES hospital_records (id)
)

;

CREATE TABLE patient_memberships (
	record_id UUID NOT NULL, 
	patient_id UUID NOT NULL, 
	site_id VARCHAR(120) NOT NULL, 
	linked_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (record_id), 
	UNIQUE (patient_id, site_id), 
	FOREIGN KEY(record_id) REFERENCES hospital_records (id), 
	FOREIGN KEY(patient_id) REFERENCES global_patients (id), 
	FOREIGN KEY(site_id) REFERENCES hospitals (id)
)

;
CREATE INDEX ix_patient_memberships_patient_id ON patient_memberships (patient_id);

CREATE TABLE record_versions (
	id UUID NOT NULL, 
	record_id UUID NOT NULL, 
	submission_id UUID NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	token_version VARCHAR(30) NOT NULL, 
	key_id VARCHAR(120) NOT NULL, 
	method VARCHAR(30) NOT NULL, 
	tokens JSONB NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (record_id, submission_id), 
	FOREIGN KEY(record_id) REFERENCES hospital_records (id), 
	FOREIGN KEY(submission_id) REFERENCES submissions (id)
)

;
CREATE INDEX ix_record_versions_submission_id ON record_versions (submission_id);
CREATE INDEX ix_record_versions_record_id ON record_versions (record_id);

CREATE TABLE match_assessments (
	id BIGSERIAL NOT NULL, 
	version_id UUID NOT NULL, 
	candidate_patient_id UUID NOT NULL, 
	candidate_record_id UUID NOT NULL, 
	score INTEGER NOT NULL, 
	evidence JSONB NOT NULL, 
	scoring_version VARCHAR(40) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(version_id) REFERENCES record_versions (id), 
	FOREIGN KEY(candidate_patient_id) REFERENCES global_patients (id), 
	FOREIGN KEY(candidate_record_id) REFERENCES hospital_records (id)
)

;
CREATE INDEX ix_match_assessments_version_id ON match_assessments (version_id);

CREATE TABLE record_tokens (
	version_id UUID NOT NULL, 
	position INTEGER NOT NULL, 
	value VARCHAR(64) NOT NULL, 
	token_version VARCHAR(30) NOT NULL, 
	key_id VARCHAR(120) NOT NULL, 
	method VARCHAR(30) NOT NULL, 
	PRIMARY KEY (version_id, position), 
	FOREIGN KEY(version_id) REFERENCES record_versions (id)
)

;
CREATE INDEX ix_token_lookup ON record_tokens (token_version, key_id, method, position, value);

CREATE TABLE review_cases (
	id UUID NOT NULL, 
	record_id UUID NOT NULL, 
	version_id UUID NOT NULL, 
	reason VARCHAR(80) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(record_id) REFERENCES hospital_records (id), 
	FOREIGN KEY(version_id) REFERENCES record_versions (id)
)

;
CREATE INDEX ix_review_cases_status ON review_cases (status);
CREATE INDEX ix_review_cases_record_id ON review_cases (record_id);
ALTER TABLE hospital_records ADD CONSTRAINT fk_latest_version FOREIGN KEY(latest_version_id) REFERENCES record_versions (id);
ALTER TABLE hospital_records ADD CONSTRAINT fk_matching_version FOREIGN KEY(matching_version_id) REFERENCES record_versions (id);
