import { useState } from 'react';
import Upload from './components/Upload';
import ReceiptsList from './components/ReceiptsList';
import Dashboard from './components/Dashboard';
import ManualExpenseModal from './components/ManualExpenseModal';
import { PlusCircle } from 'lucide-react';

function App() {
    const [refreshKey, setRefreshKey] = useState(0);
    const [manualOpen, setManualOpen] = useState(false);

    const handleUploadSuccess = () => {
        // Trigger refresh of the list
        setRefreshKey(old => old + 1);
    };

    return (
        <div className="min-h-screen bg-gray-100 py-10 px-4 sm:px-6 lg:px-8">
            <div className="max-w-7xl mx-auto space-y-8">
                <div className="flex items-center justify-between gap-4 flex-wrap">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900">OCR Expense Intelligence</h1>
                        <p className="mt-2 text-gray-600">Upload receipts or log expenses to track your spending</p>
                    </div>
                    <button
                        onClick={() => setManualOpen(true)}
                        className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-blue-600 text-white font-medium hover:bg-blue-700 shadow-sm"
                    >
                        <PlusCircle size={18} />
                        Log manual expense
                    </button>
                </div>

                <Dashboard refreshTrigger={refreshKey} />

                {/* Compact full-width uploader, then the receipts table gets the entire width. */}
                <Upload onUploadSuccess={handleUploadSuccess} />

                <ReceiptsList refreshTrigger={refreshKey} onChange={handleUploadSuccess} />
            </div>

            <ManualExpenseModal
                open={manualOpen}
                onClose={() => setManualOpen(false)}
                onCreated={handleUploadSuccess}
            />
        </div>
    )
}

export default App;
