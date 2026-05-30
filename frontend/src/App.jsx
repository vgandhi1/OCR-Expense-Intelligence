import { useState } from 'react';
import Upload from './components/Upload';
import ReceiptsList from './components/ReceiptsList';
import Dashboard from './components/Dashboard';

function App() {
    const [refreshKey, setRefreshKey] = useState(0);

    const handleUploadSuccess = () => {
        // Trigger refresh of the list
        setRefreshKey(old => old + 1);
    };

    return (
        <div className="min-h-screen bg-gray-100 py-12 px-4 sm:px-6 lg:px-8">
            <div className="max-w-5xl mx-auto space-y-8">
                <div className="text-center">
                    <h1 className="text-3xl font-bold text-gray-900">OCR Expense Intelligence</h1>
                    <p className="mt-2 text-gray-600">Upload receipts to track your spending automatically</p>
                </div>

                <Dashboard refreshTrigger={refreshKey} />

                <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                    <div className="md:col-span-1">
                        <Upload onUploadSuccess={handleUploadSuccess} />
                    </div>
                    <div className="md:col-span-2">
                        <ReceiptsList refreshTrigger={refreshKey} onChange={handleUploadSuccess} />
                    </div>
                </div>
            </div>
        </div>
    )
}

export default App;
