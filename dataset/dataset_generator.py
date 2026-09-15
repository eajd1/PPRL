import random
import sys
from datetime import datetime, timedelta

class Patient:
    patient_id = ""
    first_name = ""
    last_name = ""
    dob = ""
    weight = ""
    sex = ""
    postcode = ""
    phone = ""
    medicare = ""

    def __init__(self, patient_id, first_name, last_name, dob, weight, sex, postcode, phone, medicare):
        self.patient_id = str(patient_id)
        self.first_name = first_name
        self.last_name = last_name
        self.dob = dob
        self.weight = str(weight)
        self.sex = sex
        self.postcode = str(postcode)
        self.phone = str(phone)
        self.medicare = str(medicare)

class Record:
    patient = None
    visit_date = ""
    diagnosis_code = ""
    hospital_id = ""

    def __init__(self, patient, visit_date, diagnosis_code, hospital_id):
        self.patient = patient
        self.visit_date = visit_date
        self.diagnosis_code = str(diagnosis_code)
        self.hospital_id = str(hospital_id)



# Random date between start and end
def get_random_date(start, end):
    delta = end - start
    random_days = random.randrange(delta.days + 1)
    return start + timedelta(days=random_days)

def format_date(date, form):
    return date.strftime(form)

def get_age(date):
    current_date = datetime.now()
    return current_date.year - date.year - (
        (current_date.month, current_date.day) < 
        (date.month, date.day)
    )

def get_random_weight(age):
    # 1. Infants (0 to 12 months): 3.5 kg to ~10 kg
    if age == 0:
        mean, std_dev = 7.0, 1.5
    # 2. Toddlers (1 to 2 years): ~10 kg to 14 kg
    elif age <= 2:
        mean, std_dev = 12.0, 1.8
    # 3. Young Children (3 to 5 years): ~14 kg to 19 kg
    elif age <= 5:
        mean, std_dev = 16.5, 2.5
    # 4. Older Children (6 to 11 years)
    elif age <= 11:
        mean, std_dev = 32.0, 6.0
    # 5. Teenagers (12 to 17 years): Massive variance due to growth spurts
    elif age <= 17:
        mean, std_dev = 58.0, 10.0
    # 6. Adults (18+ years): Covers standard adult population distributions
    else:
        mean, std_dev = 76.0, 14.0

    # Generate the weight using a normal distribution
    weight = random.normalvariate(mean, std_dev)
    
    # Enforce realistic physiological floor boundaries (just in case of outliers)
    min_possible_weight = mean - (2.5 * std_dev)
    if weight < min_possible_weight:
        weight = min_possible_weight
        
    return round(weight, 1)

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

def get_record_entry(record, rand):
    return ((record.patient.first_name if random.random() > deletion_rate else "") + "," +
            (record.patient.last_name if random.random() > deletion_rate else "") + "," +
            (format_date(record.patient.dob, date_format) if random.random() > deletion_rate else "") + "," +
            (record.patient.weight if random.random() > deletion_rate else "") + "," +
            (hospital_gender_format(record.patient.sex, rand) if random.random() > deletion_rate else "") + "," +
            (record.patient.postcode if random.random() > deletion_rate else "") + "," +
            (("0" + record.patient.phone) if random.random() > deletion_rate else "") + "," +
            (record.patient.medicare if random.random() > deletion_rate else "") + "," +
            (record.diagnosis_code if random.random() > deletion_rate else "") + "," +
            (format_date(record.visit_date, date_format) if random.random() > deletion_rate else "") + "\n")



seed = 1
num_patients = 100
num_hospitals = 3
num_records_per_hospital = 100
deletion_rate = 0.05
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
last_names = []
with open("names.csv", "r") as names:
    for line in names:
        first_last = line.strip().split(",")
        first_names.append(first_last[0])
        last_names.append(first_last[1])

# Make random patients
patients = []
for i in range(0, num_patients):
    first_name = random.choice(first_names)
    last_name = random.choice(last_names)
    dob = get_random_date(datetime(1900, 1, 1), datetime(2026, 1, 1))
    weight = get_random_weight(get_age(dob))
    sex = random.choice(["m", "f", "?"])
    postcode = random.randint(2000, 2999)
    phone = random.randint(400000000, 499999999)
    medicare = random.randint(1000000000, 9999999999)
    patients.append(Patient(i, first_name, last_name, dob,
                            weight, sex, postcode, phone, medicare))

hospital_rng = []
for h in range(0, num_hospitals):
    hospital_rng.append(random.randint(0, num_hospitals * 1000))

# Make multiple records for each patient
hospital_records = []
for h in range(0, num_hospitals):
    records = []
    for i in range(0, num_records_per_hospital):
        patient = random.choice(patients)
        records.append(Record(patient,
                              get_random_date(patient.dob, datetime(2026, 1, 1)),
                              random.randint(1, 999999), h))
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
        date_format = random.choice(date_formats)
        for j in range(0, len(hospital_records[i])):
            record = hospital_records[i][j]
            line = get_record_entry(record, hospital_rng[i])
            file.write(line)

