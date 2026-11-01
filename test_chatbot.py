from agents.dataset_chatbot_agent import answer_dataset_question

csv_path = input("Enter the full path of your CSV file: ")
question = input("Ask a question about the dataset: ")

answer = answer_dataset_question(csv_path, question)

print("\nChatbot Answer:")
print(answer)