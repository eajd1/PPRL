import random
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

    def __str__(self):
        return self.first_name

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

    def __str__(self):
        pass



# Random date between 1900 and 2026
def get_random_date():
    start = datetime(1900, 1, 1)
    end = datetime(2026, 1, 1)
    delta = end - start
    random_days = random.randrange(delta.days + 1)
    return start + timedelta(days=random_days)

def get_age(date):
    current_date = datetime.now()
    return current_date.year - date.year - (
        (current_date.month, current_date.day) < 
        (date.month, date.day)
    )

def get_random_weight(age):
    # TODO have realistic weight/age distribution
    return random.randint(30, 300)


first_names = []
last_names = []
with open("names.csv", "r") as names:
    for line in names:
        first_last = line.strip().split(",")
        first_names.append(first_last[0])
        last_names.append(first_last[1])

sexes = ["m", "f", "Male", "Female", "male", "female"]
# Make random patients
num_patients = 100
patients = []
for i in range(0, num_patients):
    first_name = random.choice(first_names)
    last_name = random.choice(last_names)
    dob = get_random_date()
    weight = get_random_weight(get_age(dob))
    sex = random.choice(sexes)
    postcode = random.randint(2000, 2999)
    phone = random.randint(400000000, 499999999)
    medicare = random.randint(1000000000, 9999999999)
    patients.append(Patient(i, first_name, last_name, dob,
                            weight, sex, postcode, phone, medicare))

# Make multiple records for each patient
num_hospitals = 3
num_records_per_hospital = 100
hospital_records = []
for h in range(0, num_hospitals):
    records = []
    for i in range(0, num_records_per_hospital):
        records.append(Record(random.choice(patients), get_random_date(),
                                            random.randint(1, 999999), h))
    hospital_records.append(records)

for i in range(0, len(hospital_records)):
    with open("hospital" + str(i + 1) + ".csv", "w") as file:
        for j in range(0, len(hospital_records[i])):
            record = hospital_records[i][j]
            line = (record.patient.first_name + "," +
                    record.patient.last_name + "," +
                    str(record.patient.dob) + "," +
                    record.patient.weight + "," +
                    record.patient.sex + "," +
                    record.patient.postcode + "," +
                    record.patient.phone + "," +
                    record.patient.medicare + "," +
                    record.diagnosis_code + "," +
                    str(record.visit_date) + "," +
                    record.hospital_id + "\n")
            file.write(line)

