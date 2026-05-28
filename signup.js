// 1. INITIALIZE SUPABASE (Switching back to VELVET)
const supabaseUrl = 'https://ivrztuodamhbgbuwwesg.supabase.co'; 
const supabaseKey = 'sb_publishable__LtZr-fkLALBDibnIFImnA_9dU17BOp'; // ENSURE THIS IS THE VELVET ANON KEY

const supabaseClient = supabase.createClient(supabaseUrl, supabaseKey);

// 2. GET ELEMENTS
const authForm = document.getElementById('authForm');
const submitBtn = document.getElementById('submitBtn');
const toggleContainer = document.getElementById('toggleMode');
const errorMessage = document.getElementById('errorMessage');

let isSignUpMode = false; 

// 3. THE TOGGLE LOGIC
if (toggleContainer) {
    toggleContainer.addEventListener('click', function(e) {
        e.preventDefault();
        isSignUpMode = !isSignUpMode;
        submitBtn.innerText = isSignUpMode ? "Create Account" : "Sign In";
        this.innerHTML = isSignUpMode ? 
            'Already have an account? <span>Sign in</span>' : 
            'Don\'t have an account? <span>Sign up</span>';
        if (errorMessage) errorMessage.style.display = 'none';
    });
}

// 4. THE ROUTING ENGINE
async function handleUserRouting(user) {
    // Check Velvet's profiles table for a username
    const { data: profile } = await supabaseClient
        .from('profiles')
        .select('username')
        .eq('id', user.id)
        .single();

    // If no username exists yet -> info.html
    if (!profile || !profile.username) {
        window.location.href = 'info.html';
    } else {
        // Established user -> home.html
        window.location.href = 'home.html';
    }
}

// 5. THE AUTH LOGIC
if (authForm) {
    authForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('email').value.trim();
        const password = document.getElementById('password').value;

        submitBtn.innerText = "Processing...";
        submitBtn.disabled = true;

        try {
            if (isSignUpMode) {
                // SIGN UP
                const { data, error } = await supabaseClient.auth.signUp({ email, password });
                if (error) throw error;
                // Fresh Velvet accounts go to setup
                window.location.href = 'info.html'; 
            } else {
                // SIGN IN
                const { data, error } = await supabaseClient.auth.signInWithPassword({ email, password });
                if (error) throw error;
                // Check routing
                await handleUserRouting(data.user);
            }
        } catch (err) {
            if (errorMessage) {
                errorMessage.style.display = 'block';
                errorMessage.innerText = err.message;
            }
            submitBtn.innerText = isSignUpMode ? "Create Account" : "Sign In";
            submitBtn.disabled = false;
        }
    });
}
