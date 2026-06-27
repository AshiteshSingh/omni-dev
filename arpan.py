import matplotlib.pyplot as plt

# Data extracted from the case study
expenses = [
    'Purchases (Raw Materials)', 
    'Staff Salaries', 
    'Facility Rent', 
    'Depreciation (Wear & Tear)', 
    'Wages (Direct Labor)', 
    'Insurance'
]

# Corresponding amounts in ₹
amounts = [700000, 160000, 120000, 110000, 80000, 15000]

# 'Explode' the first slice (Purchases) to highlight it as the largest cost center
explode = (0.1, 0, 0, 0, 0, 0)  

# Create the figure
plt.figure(figsize=(10, 7))

# Generate the pie chart
plt.pie(
    amounts, 
    labels=expenses, 
    explode=explode, 
    autopct='%1.1f%%', # Automatically formats the percentages to 1 decimal place
    startangle=140,    # Rotates the start of the pie chart for better readability
    colors=['#ff9999','#66b3ff','#99ff99','#ffcc99','#c2c2f0','#ffb3e6'] # Custom color palette
)

# Add a title
plt.title('Crust & Crumb Bakers - Annual Expense Visualization', fontsize=14, pad=20)

# Equal aspect ratio ensures that pie is drawn as a perfect circle
plt.axis('equal')  

# Display the plot
plt.tight_layout()
plt.show()