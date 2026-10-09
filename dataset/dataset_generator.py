import random
import string
import sys
from datetime import datetime, timedelta

class Patient:
    patient_id = ""
    first_name = ""
    last_name = ""
    dob = ""
    sex = ""
    postcode = ""
    phone = ""
    medicare = ""
    email = ""
    middle_name = ""

    def __init__(self, patient_id, first_name, last_name, dob, sex, postcode, phone, medicare, email, middle_name):
        self.patient_id = str(patient_id)
        self.first_name = first_name
        self.last_name = last_name
        self.dob = dob
        self.sex = sex
        self.postcode = str(postcode)
        self.phone = str(phone)
        self.medicare = str(medicare)
        self.email = email
        self.middle_name = middle_name

class Record:
    patient = None
    visit_date = ""
    hospital_id = ""
    admission_date = ""
    discharge_date = ""
    diagnosis = ""
    treatment = ""
    note = ""
    phone = ""

    def __init__(self, patient, visit_date, hospital_id, admission_date, discharge_date, diagnosis, treatment, note, phone):
        self.patient = patient
        self.visit_date = visit_date
        self.hospital_id = str(hospital_id)
        self.admission_date = admission_date
        self.discharge_date = discharge_date
        self.diagnosis = diagnosis
        self.treatment = treatment
        self.note = note
        self.phone = phone



# Random date between start and end
def get_random_date(start, end):
    delta = end - start
    random_days = random.randrange(delta.days + 1)
    return start + timedelta(days=random_days)

def format_date(date, form):
    try:
        return date.strftime(form).strip()
    except:
        return ""

def get_age(date):
    current_date = datetime.now()
    return current_date.year - date.year - (
        (current_date.month, current_date.day) < 
        (date.month, date.day)
    )

def random_string(length):
    characters = string.ascii_letters
    return "".join(random.choices(characters, k=length))

def hospital_gender_format(gender, rand):
    if gender == "m":
        i = rand % 5
        return ["m", "M", "male", "Male", "MALE"][i]
    elif gender == "f":
        i = rand % 5
        return ["f", "F", "female", "Female", "FEMALE"][i]
    else:
        i = rand % 5
        return ["", "?", "unspecified", "Unspecified", "Unknown"][i]

def get_record_entry(record, rand, date):
    return ((random.choice(record.patient.first_name) if random.random() > deletion_rate else "") + "," +
            (random.choice(record.patient.last_name) if random.random() > deletion_rate else "") + "," +
            (random.choice(record.patient.middle_name) if random.random() > deletion_rate else "") + "," +
            (record.patient.email if random.random() > deletion_rate else "") + "," +
            (format_date(record.patient.dob, date) if random.random() > deletion_rate else "") + "," +
            (hospital_gender_format(record.patient.sex, rand) if random.random() > deletion_rate else "") + "," +
            (record.patient.postcode if random.random() > deletion_rate else "") + "," +
            (("0" + record.patient.phone) if random.random() > deletion_rate else "") + "," +
            (record.patient.medicare if random.random() > deletion_rate else "") + "," +
            (format_date(record.visit_date, date) if random.random() > deletion_rate else "") + "," +
            (format_date(record.admission_date, date) if random.random() > deletion_rate else "") + "," +
            (format_date(record.discharge_date, date) if random.random() > deletion_rate else "") + "," +
            (record.diagnosis if random.random() > deletion_rate else "") + "," +
            (record.treatment if random.random() > deletion_rate else "") + "," +
            (record.note if random.random() > deletion_rate else "") + "," +
            (record.phone if random.random() > deletion_rate else "") + "\n")


seed = 1
num_patients = 100
num_hospitals = 3
num_records_per_hospital = 100
deletion_rate = 0.05
admission_rate = 0.1
if len(sys.argv) == 2:
    seed = int(sys.argv[1])
elif len(sys.argv) == 6:
    seed = int(sys.argv[1])
    num_patients = int(sys.argv[2])
    num_hospitals = int(sys.argv[3])
    num_records_per_hospital = int(sys.argv[4])
    deletion_rate = float(sys.argv[5])
else:
    print("Arguments are: <seed> <num patients> <num hospitals> <num records per hospital> <data deletion rate>")
    print("Defaulting too: 1, 100, 3, 100")

random.seed(seed)



first_names = []
with open("first_names.csv", "r") as names:
    for line in names:
        first_names.append(line.strip().split(","))
last_names = []
with open("last_names.csv", "r") as names:
    for line in names:
        last_names.append(line.strip().split(","))

# Make random patients
patients = []
for i in range(0, num_patients):
    first_name = random.choice(first_names)
    last_name = random.choice(last_names)
    dob = get_random_date(datetime(1900, 1, 1), datetime(2026, 1, 1))
    sex = random.choice(["m", "f", "?"])
    postcode = random.randint(2000, 2999)
    phone = random.randint(400000000, 499999999)
    medicare = random.randint(1000000000, 9999999999)
    email = random_string(10) + "@" + random_string(6) + ".com"
    middle_name = random.choice(first_names) if random.random() > 0.5 else random.choice(last_names)
    patients.append(Patient(i, first_name, last_name, dob, sex,
                            postcode, phone, medicare, email, middle_name))

hospital_rng = []
for h in range(0, num_hospitals):
    hospital_rng.append(random.randint(400000000, 499999999)) # Also acts as hospital phone

# Make multiple records for each patient
hospital_records = []
for h in range(0, num_hospitals):
    records = []
    for i in range(0, num_records_per_hospital):
        patient = random.choice(patients)
        visit_date = get_random_date(patient.dob, datetime(2026, 1, 1))
        admitted = random.random() < admission_rate
        records.append(Record(patient,
                              visit_date,
                              h,
                              visit_date if admitted else "",
                              get_random_date(visit_date, datetime(2026, 1, 1)) if admitted else "",
                              "diagnosis" + random_string(hospital_rng[h] % 5 * 10),
                              "treatment" + random_string(hospital_rng[h] % 5 * 10),
                              random_string(hospital_rng[h] % 5 * 30),
                              "0" + str(hospital_rng[h])
                              ))
    hospital_records.append(records)

# Ground Truth
with open("ground_truth.csv", "w") as file:
    text = ""
    for i in range(0, len(hospital_records)):
        for j in range(0, len(hospital_records[i])):
            text = text + str(hospital_records[i][j].patient.patient_id) + ","
        text = text[:-1]
        text = text + "\n"
    file.write(text)

# Randomise Data
date_formats = ["%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y", "%e/%m/%Y", "%e-%m-%Y"]
# TODO randomise more (add mistakes)
for i in range(0, len(hospital_records)):
    with open("hospital" + str(i + 1) + ".csv", "w") as file:
        file.write("first_name, last_name, middle_name, email, dob, sex, postcode, phone, medicare, visit_date, admission_date, discharge_date, diagnosis, treatment, note, hospital_phone\n")
        date_format = random.choice(date_formats)
        for j in range(0, len(hospital_records[i])):
            record = hospital_records[i][j]
            line = get_record_entry(record, hospital_rng[i], date_format)
            file.write(line)

