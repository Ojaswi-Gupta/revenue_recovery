// Auto-refresh stats using HTMX events
document.body.addEventListener('htmx:afterOnLoad', function(evt) {
    if (evt.detail.target.id === 'dashboard-stats') {
        console.log('Stats auto-refreshed');
    }
});

// Toast notification system
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container') || createToastContainer();
    
    const toast = document.createElement('div');
    toast.className = `toast toast-${type} px-4 py-2 rounded-md shadow-lg mb-2 text-white transition-opacity duration-300`;
    
    if (type === 'success') toast.style.backgroundColor = '#10b981';
    else if (type === 'error') toast.style.backgroundColor = '#ef4444';
    else if (type === 'warning') toast.style.backgroundColor = '#f59e0b';
    else toast.style.backgroundColor = '#3b82f6';
    
    toast.textContent = message;
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'fixed bottom-4 right-4 z-50 flex flex-col-reverse';
    document.body.appendChild(container);
    return container;
}

// Confirm dialogs for destructive actions
function confirmAction(message, callback) {
    if (window.confirm(message)) {
        callback();
    }
}

// Number formatting (Indian numbering)
function formatIndianNumber(num) {
    if (isNaN(num)) return num;
    const parts = num.toString().split('.');
    let integerPart = parts[0];
    const decimalPart = parts.length > 1 ? '.' + parts[1] : '';
    
    if (integerPart.length > 3) {
        const lastThree = integerPart.substring(integerPart.length - 3);
        const otherNumbers = integerPart.substring(0, integerPart.length - 3);
        integerPart = otherNumbers.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + lastThree;
    }
    
    return integerPart + decimalPart;
}

// Simple chart rendering using inline SVG
function renderSparkline(elementId, data) {
    const el = document.getElementById(elementId);
    if (!el || !data || data.length === 0) return;
    
    const width = el.clientWidth || 200;
    const height = el.clientHeight || 50;
    
    const max = Math.max(...data);
    const min = Math.min(...data);
    const range = max - min || 1;
    
    const points = data.map((val, i) => {
        const x = (i / (data.length - 1)) * width;
        const y = height - ((val - min) / range) * height;
        return `${x},${y}`;
    }).join(' ');
    
    el.innerHTML = `
        <svg width="100%" height="100%" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
            <polyline points="${points}" fill="none" stroke="#3b82f6" stroke-width="2" />
        </svg>
    `;
}

// Export table to CSV function
function exportTableToCSV(tableId, filename) {
    const table = document.getElementById(tableId);
    if (!table) return;
    
    let csv = [];
    const rows = table.querySelectorAll('tr');
    
    for (let i = 0; i < rows.length; i++) {
        let row = [], cols = rows[i].querySelectorAll('td, th');
        
        for (let j = 0; j < cols.length; j++) {
            let data = cols[j].innerText.replace(/(\r\n|\n|\r)/gm, '').replace(/(\s\s)/gm, ' ');
            data = data.replace(/"/g, '""');
            row.push('"' + data + '"');
        }
        csv.push(row.join(','));
    }
    
    const csvFile = new Blob([csv.join('\n')], { type: 'text/csv' });
    const downloadLink = document.createElement('a');
    
    downloadLink.download = filename || 'export.csv';
    downloadLink.href = window.URL.createObjectURL(csvFile);
    downloadLink.style.display = 'none';
    
    document.body.appendChild(downloadLink);
    downloadLink.click();
    document.body.removeChild(downloadLink);
}
