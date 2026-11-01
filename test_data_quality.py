
from agents.data_quality_agent import analyze_data_quality

csv_path = input("Enter the CSV file path: ").strip().strip('"')

try:
    report = analyze_data_quality(csv_path)

    print("\n========== DATA QUALITY REPORT ==========")
    print("Quality Score:", report["quality_score"], "/ 100")
    print("Status:", report["quality_status"])
    print("Rows:", report["total_rows"])
    print("Columns:", report["total_columns"])
    print("Missing Cells:", report["missing_cells"])
    print("Duplicate Rows:", report["duplicate_rows"])

    print("\n---------- COLUMN DETAILS ----------")
    for column in report["column_report"]:
        print(
            column["column"],
            "| Type:", column["data_type"],
            "| Missing:", column["missing_values"],
            "| Missing %:", column["missing_percentage"],
            "| Unique:", column["unique_values"]
        )

    print("\n---------- RECOMMENDATIONS ----------")
    for recommendation in report["recommendations"]:
        print("-", recommendation)

except Exception as error:
    print("Error:", error)
