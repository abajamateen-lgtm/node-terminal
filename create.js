// create.js - Decoupled Compose Logical Engine Architecture
const SB_URL = 'https://ivrztuodamhbgbuwwesg.supabase.co';
const SB_KEY = 'sb_publishable__LtZr-fkLALBDibnIFImnA_9dU17BOp';

// Initialize the isolated client instance
const supabaseClient = supabase.createClient(SB_URL, SB_KEY);

// Cache layout container references
const textarea = document.getElementById('postInput');
const postBtn = document.getElementById('postBtn');
const cancelBtn = document.getElementById('cancelBtn');
let currentUserId = null;

/**
 * 1. INITIALIZATION & SESSION RESOLUTION PIPELINE
 * Bridges file:// sandbox tracking gaps using three fallback validation layers
 */
async function parseActiveUser() {
    try {
        // Layer A: Attempt reading credentials from application global cookie cache
        const sessionToken = JSON.parse(localStorage.getItem('sb-ivrztuodamhbgbuwwesg-auth-token'));
        if (sessionToken && sessionToken.user) {
            currentUserId = sessionToken.user.id;
        } 
        
        // Layer B: Fallback to reading URL query string parameter token passed from feed context navigation
        if (!currentUserId) {
            const urlParams = new URLSearchParams(window.location.search);
            currentUserId = urlParams.get('sender');
        }

        // Layer C: Emergency request to live auth server signature matching records
        if (!currentUserId) {
            const { data: { user } } = await supabaseClient.auth.getUser();
            if (user) currentUserId = user.id;
        }

        // If identity context resolves successfully, map user visual parameters
        if (currentUserId) {
            loadUserMiniProfile(currentUserId);
        }
    } catch (e) {
        console.error("Failed to safely decode active user token chain:", e);
    }
}

/**
 * 2. USER DECORATION LAYER
 * Resolves user avatar profiles mapping data objects directly from storage
 */
async function loadUserMiniProfile(userId) {
    const { data, error } = await supabaseClient
        .from('profiles')
        .select('avatar_url')
        .eq('id', userId)
        .single();

    if (!error && data && data.avatar_url) {
        const avatarDiv = document.getElementById('userAvatarContainer');
        const fullUrl = data.avatar_url.startsWith('http') 
            ? data.avatar_url 
            : `https://ivrztuodamhbgbuwwesg.supabase.co/storage/v1/object/public/post-images/${data.avatar_url}`;
        
        if (avatarDiv) {
            avatarDiv.innerHTML = `<img src="${fullUrl}" style="width:100%; height:100%; object-fit:cover;">`;
        }
    }
}

/**
 * 3. INTERACTIVE EVENT ATTRIBUTE MONITORING
 */
textarea.addEventListener('input', () => {
    const hasText = textarea.value.trim().length > 0;
    postBtn.disabled = !hasText;
});

cancelBtn.addEventListener('click', () => {
    window.history.back();
});

postBtn.addEventListener('click', async () => {
    const content = textarea.value.trim();
    if (!content) return;

    // Shift interactive UI processing state to prevent double execution requests
    postBtn.innerText = "Posting...";
    postBtn.disabled = true;

    try {
        // Safe check verification backup check directly before committing insertion script 
        if (!currentUserId) {
            const urlParams = new URLSearchParams(window.location.search);
            currentUserId = urlParams.get('sender');
        }

        if (!currentUserId) {
            throw new Error("Authentication token signature missing. Please return to home.html to re-sync.");
        }

        // Process data insertion to database cluster mapping layout
        const { error } = await supabaseClient
            .from('posts')
            .insert([{ 
                content: content, 
                user_id: currentUserId,
                created_at: new Date().toISOString() 
            }]);

        if (error) throw error;
        
        // Clean pipeline transition backward operation on successful dispatch loop execution
        window.history.back(); 

    } catch (err) {
        alert("Node Connection Failure: " + err.message);
        postBtn.innerText = "Post";
        postBtn.disabled = false;
    }
});

// Fire tracking sync sequencing routine upon architecture document readiness initialization
document.addEventListener("DOMContentLoaded", parseActiveUser);


