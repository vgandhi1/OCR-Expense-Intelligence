import React, { useState } from 'react';
import client from '../api/client';
import { CATEGORIES } from '../constants';
import { Loader2, X } from 'lucide-react';

const today = () => new Date().toISOString().slice(0, 10);

const emptyForm = () => ({
    merchant_name: '',
    total_amount: '',
    date: today(),
    category: 'Uncategorized',
    notes: '',
});

const ManualExpenseModal = ({ open, onClose, onCreated }) => {
    const [form, setForm] = useState(emptyForm());
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    if (!open) return null;

    const set = (field) => (e) => setForm({ ...form, [field]: e.target.value });

    const submit = async (e) => {
        e.preventDefault();
        setError('');

        const amount = Number(form.total_amount);
        if (!form.merchant_name.trim()) return setError('Merchant is required.');
        if (!Number.isFinite(amount) || amount < 0) return setError('Enter a valid amount.');
        if (!form.date) return setError('Date is required.');

        setSaving(true);
        try {
            await client.post('/expenses/manual', {
                merchant_name: form.merchant_name.trim(),
                total_amount: amount,
                date: new Date(form.date).toISOString(),
                category: form.category,
                notes: form.notes.trim() || null,
            });
            setForm(emptyForm());
            onCreated?.();
            onClose?.();
        } catch (err) {
            console.error('Error creating manual expense:', err);
            setError(err.response?.data?.detail || 'Could not save expense.');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
            onClick={onClose}
        >
            <div
                className="w-full max-w-md bg-white rounded-xl shadow-xl"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between px-6 py-4 border-b">
                    <h2 className="text-lg font-semibold text-gray-800">Log manual expense</h2>
                    <button onClick={onClose} className="p-1 rounded hover:bg-gray-100 text-gray-500" title="Close">
                        <X size={20} />
                    </button>
                </div>

                <form onSubmit={submit} className="px-6 py-5 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-600 mb-1">Merchant</label>
                        <input
                            type="text"
                            value={form.merchant_name}
                            onChange={set('merchant_name')}
                            placeholder="e.g. Corner Cafe"
                            className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                            autoFocus
                        />
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-600 mb-1">Amount</label>
                            <input
                                type="number"
                                step="0.01"
                                min="0"
                                value={form.total_amount}
                                onChange={set('total_amount')}
                                placeholder="0.00"
                                className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-600 mb-1">Date</label>
                            <input
                                type="date"
                                value={form.date}
                                onChange={set('date')}
                                className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                            />
                        </div>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-600 mb-1">Category</label>
                        <select
                            value={form.category}
                            onChange={set('category')}
                            className="w-full border rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:outline-none"
                        >
                            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-600 mb-1">Notes <span className="text-gray-400">(optional)</span></label>
                        <input
                            type="text"
                            value={form.notes}
                            onChange={set('notes')}
                            placeholder="e.g. Team lunch"
                            className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
                        />
                    </div>

                    {error && <p className="text-sm text-red-600">{error}</p>}

                    <div className="flex justify-end gap-2 pt-2">
                        <button type="button" onClick={onClose} className="px-4 py-2 text-sm rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-100">
                            Cancel
                        </button>
                        <button
                            type="submit"
                            disabled={saving}
                            className="inline-flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                        >
                            {saving && <Loader2 size={16} className="animate-spin" />}
                            Save expense
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
};

export default ManualExpenseModal;
