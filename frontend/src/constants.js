// Expense categories shared across the receipts table, manual-expense modal,
// and budget panel so the option list stays in one place.
export const CATEGORIES = [
    'Groceries', 'Dining', 'Transport', 'Shopping',
    'Utilities', 'Entertainment', 'Health', 'Uncategorized',
];

// Current month as "YYYY-MM" (matches the budget month key).
export const currentMonth = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
};
