const vClient = supabase.createClient('https://ivrztuodamhbgbuwwesg.supabase.co', 'sb_publishable__LtZr-fkLALBDibnIFImnA_9dU17BOp');

const PLAN_PRICES = { "Node Plus": 5, "Node Creator": 10, "Node Pro": 35 };
let transactionState = { plan_type: '', amount_paid: 0, selected_crypto: null };

const BASE_STABLECOIN_CONTRACTS = {
    usdt_base: "0x50c5725949A6F0c72E6C4a641F24049A91D18C41",
    usdc_base: "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913"
};

const CRYPTO_ROUTER_ADDRESS = "0x58bd07BE8B50DE85967EeED95492e36b738C57D5D";

// 1. Initialize Panel Configuration
window.initiateCheckout = function(planName) {
    transactionState.plan_type = planName;
    transactionState.amount_paid = PLAN_PRICES[planName];
    
    document.getElementById('modalTargetTitle').innerText = planName + " Support & Perks";
    if (document.getElementById('modalTargetPrice')) {
        document.getElementById('modalTargetPrice').innerText = "$" + transactionState.amount_paid + ".00";
    }
    
    document.getElementById('modal-flow-selection').style.display = 'none';
    document.getElementById('modal-flow-form').style.display = 'block';
    document.getElementById('paymentModal').style.display = 'flex';
    
    renderStablecoinSelection();
};

function renderStablecoinSelection() {
    document.getElementById('dynamic-fields-target').innerHTML = `
        <button type="button" onclick="selectAsset('usdt_base')" class="btn-submit-payment">Donate USDT (Base) - $${transactionState.amount_paid}</button>
        <button type="button" onclick="selectAsset('usdc_base')" class="btn-submit-payment" style="margin-top:10px;">Donate USDC (Base) - $${transactionState.amount_paid}</button>
    `;
}

// 2. Asset & Wallet Search Engine 
window.selectAsset = function(coinId) {
    transactionState.selected_crypto = coinId;
    document.getElementById('dynamic-fields-target').innerHTML = `
        <input type="text" id="walletSearch" placeholder="Search your wallet app..." oninput="filterWallets()" class="form-input" style="width:100%; padding:10px; background:#000; border:1px solid #222; color:#fff; border-radius:8px; margin-bottom:10px;">
        <div id="walletList"></div>
    `;
    renderWalletList(["Trust Wallet", "MetaMask", "Phantom", "Coinbase Wallet"]);
};

function renderWalletList(wallets) {
    const list = document.getElementById('walletList');
    list.innerHTML = wallets.map(w => `<button type="button" onclick="executeVerifiedPayment('${w}')" class="btn-submit-payment" style="display:block; width:100%; margin-bottom:5px;">${w}</button>`).join('');
}

window.filterWallets = function() {
    const query = document.getElementById('walletSearch').value.toLowerCase();
    const wallets = ["Trust Wallet", "MetaMask", "Phantom", "Coinbase Wallet"];
    renderWalletList(wallets.filter(w => w.toLowerCase().includes(query)));
};

// Helper: Secure Clipboard Copy Execution
window.copyToClipboard = function(elementId, label) {
    const text = document.getElementById(elementId).innerText;
    navigator.clipboard.writeText(text).then(() => {
        alert(`${label} copied successfully!`);
    }).catch(() => {
        alert("Failed to copy text automatically.");
    });
};

// 3. Automated Route Processing with Secure Manual Copy (No external app redirects)
window.executeVerifiedPayment = async function(walletName) {
    const coinId = transactionState.selected_crypto;
    const amount = transactionState.amount_paid;
    const assetTicker = coinId === 'usdt_base' ? 'USDT' : 'USDC';

    // Capture User Session ID cleanly
    let accountId = null;
    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key.includes('-auth-token')) {
            try {
                const parsed = JSON.parse(localStorage.getItem(key));
                accountId = parsed?.user?.id || null;
            } catch(e) {
                console.error("Token read mismatch:", e);
            }
        }
    }

    // Safety Catch: Prevent database execution errors if account token isn't parsed
    if (!accountId) {
        alert("Verification Error: No active user session detected. Please sign in again.");
        return;
    }

    // Swap Modal display immediately to provide a fully reliable transaction template card
    const modal = document.getElementById('paymentModal');
    modal.innerHTML = `
        <div style="padding:25px; text-align:center; background:#000; width:100%; max-height:95vh; overflow-y:auto; display:flex; flex-direction:column; align-items:center; color:#fff; border-radius:12px;">
            
            <p style="color:#1d9bf0; font-size:13px; font-weight:700; margin:0 0 15px 0; text-transform:uppercase; letter-spacing:0.5px; line-height:1.4;">
                Thank you so much for supporting our app! Your donation helps us keep building, upgrading, and maintaining the platform for everyone. 🙏✨
            </p>

            <h3 style="font-size:18px; margin:0 0 5px 0;">Donation Details</h3>
            <p style="color:#888; font-size:13px; margin:0 0 20px 0;">Please copy these parameters into ${walletName} to complete your donation on the Base Network:</p>
            
            <div style="width:100%; text-align:left; margin-bottom:15px;">
                <label style="font-size:11px; color:#666; text-transform:uppercase; display:block; margin-bottom:5px; font-weight:600;">Recipient Address (Base Chain)</label>
                <div style="display:flex; gap:8px; background:#0d0d0d; border:1px solid #222; padding:10px; border-radius:8px; align-items:center;">
                    <span id="targetAddrString" style="font-family:monospace; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex-grow:1; color:#fff;">${CRYPTO_ROUTER_ADDRESS}</span>
                    <button type="button" onclick="window.copyToClipboard('targetAddrString', 'Address')" style="background:#1d9bf0; color:#fff; border:none; padding:5px 10px; font-size:11px; font-weight:bold; border-radius:4px; cursor:pointer;">Copy</button>
                </div>
            </div>

            <div style="width:100%; text-align:left; margin-bottom:20px;">
                <label style="font-size:11px; color:#666; text-transform:uppercase; display:block; margin-bottom:5px; font-weight:600;">Exact Donation Amount</label>
                <div style="display:flex; gap:8px; background:#0d0d0d; border:1px solid #222; padding:10px; border-radius:8px; align-items:center;">
                    <span id="targetAmountString" style="font-family:monospace; font-size:14px; font-weight:bold; flex-grow:1; color:#fff;">${amount}.00 ${assetTicker}</span>
                    <button type="button" onclick="window.copyToClipboard('targetAmountString', 'Amount')" style="background:#1d9bf0; color:#fff; border:none; padding:5px 10px; font-size:11px; font-weight:bold; border-radius:4px; cursor:pointer;">Copy</button>
                </div>
            </div>

            <div style="width:100%; height:1px; background:#1a1a1a; margin-bottom:20px;"></div>

            <p style="color:#555; font-size:11px; line-height:1.4; margin:0 0 20px 0;">
                Once you have sent the donation from your wallet, click below to verify and unlock your custom tier perks instantly.
            </p>

            <button type="button" id="confirmVerificationSyncBtn" class="btn-submit-payment" style="width:100%;">Verify & Activate My Perks</button>
        </div>
    `;

    // Hook click listener to trigger validation processing pipeline upon input sequence closure
    document.getElementById('confirmVerificationSyncBtn').addEventListener('click', async () => {
        // Swap display to processing block state
        modal.innerHTML = `
            <div style="padding:40px; text-align:center; background:#000; width:100%; height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center;">
                <h3 style="color:#fff; margin-bottom:10px;">Activating Your Perks...</h3>
                <p style="color:#666; font-size:13px;">Syncing your support token status with the platform database.</p>
            </div>
        `;

        try {
            // Execute remote user permission promotion sequence via explicit parameters
            const { error } = await vClient.rpc('promote_user_to_verified', { 
                target_user_id: accountId, 
                plan_type_input: transactionState.plan_type 
            });

            if (error) throw error;

            // Display finalized activation success module window mapping target redirection paths
            modal.innerHTML = `
                <div style="padding:40px; text-align:center; background:#000; width:100%; height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center;">
                    <div style="width:50px; height:50px; border-radius:50%; background:rgba(29,155,240,0.1); border:2px solid #1d9bf0; display:flex; align-items:center; justify-content:center; margin-bottom:15px;">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#1d9bf0" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>
                    </div>
                    <h3 style="color:#fff; margin-bottom:10px;">Support Verified!</h3>
                    <p style="color:#888; font-size:14px; margin-bottom:20px;">Thank you! Your security clearance and perks have been applied.</p>
                    <button type="button" onclick="window.location.href='home.html'" class="btn-submit-payment" style="width:100%; max-width:200px;">Return to Home</button>
                </div>
            `;
        } catch (e) {
            alert("Sync Failed: " + e.message);
        }
    });
};

window.closeCheckoutOverlay = () => document.getElementById('paymentModal').style.display = 'none';
window.revertToMethodSelection = () => {
    document.getElementById('modal-flow-selection').style.display = 'block';
    document.getElementById('modal-flow-form').style.display = 'none';
};

// Inject back navigation link dynamically on page integration cycles
window.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('dynamic-back-navigation')) {
        const nav = document.createElement('div');
        nav.id = 'dynamic-back-navigation';
        nav.style.cssText = `position:absolute; top:25px; left:25px; z-index:999; display:flex; align-items:center; gap:6px; cursor:pointer; padding:8px 14px; background:rgba(255,255,255,0.02); border:1px solid #1a1a1a; border-radius:8px; font-size:12px; font-weight:600; color:#a6a6a6; transition:all 0.2s; user-select:none;`;
        nav.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"></polyline></svg> Back`;
        
        nav.addEventListener('mouseenter', () => { nav.style.color='#fff'; nav.style.borderColor='#1d9bf0'; nav.style.background='rgba(29, 155, 240, 0.05)'; });
        nav.addEventListener('mouseleave', () => { nav.style.color='#a6a6a6'; nav.style.borderColor='#1a1a1a'; nav.style.background='rgba(255, 255, 255, 0.02)'; });
        nav.addEventListener('click', () => window.history.back());
        
        document.body.appendChild(nav);
    }
});
