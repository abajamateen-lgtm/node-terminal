// algorithm.js - The Algorithmic Velocity Engine

// 1. Initialize global window caches so they survive page actions but stay isolated
if (!window.sessionSeenList) {
    window.sessionSeenList = new Set();
}
if (!window.liveLikeCache) {
    window.liveLikeCache = {}; // Format: { post_id: extra_likes_count }
}

/**
 * Full Algorithmic Rules Matrix Calculation
 * Calculates a post's priority score using live Supabase counts combined with our instant cache
 */
export function computePostScore(post) {
    // Read base parameters from Supabase
    const dbLikes = Number(post.likes_count || 0);
    const dbComments = Number(post.comments_count || 0);
    
    // Read from our fast live cache to counter any database saving delays
    const instantLikes = Number(window.liveLikeCache[post.id] || 0);
    
    const totalLikes = dbLikes + instantLikes;
    const totalComments = dbComments; // Comments save perfectly, so we read directly from DB

    // Rule 1: Apply Perk Reach Weights
    // Base Placement Multiplier starts at 1.0
    let rankingScore = 1.0;
    
    // Like Event Weight: Adds +30% reach value per like
    rankingScore += (totalLikes * 0.30);
    
    // Comment Event Weight: Adds +60% reach value per comment
    rankingScore += (totalComments * 0.60);

    // Rule 2: Chronological Decay (Younger items naturally stay elevated on the timeline)
    const ageInHours = (Math.abs(new Date() - new Date(post.created_at || Date.now())) / (1000 * 60 * 60));
    const decayFactor = 1 / (ageInHours + 2);

    return rankingScore * decayFactor;
}

/**
 * Processes and prioritizes the main timeline feed array for home.html
 */
export function processMainFeed(postsArray) {
    if (!Array.isArray(postsArray)) return [];

    return postsArray
        // Rule 3: Single Exposure Main Feed Rule (Hide posts already seen by user in this session)
        .filter(post => !window.sessionSeenList.has(post.id))
        .map(post => ({
            data: post,
            rank: computePostScore(post)
        }))
        // Order highest algorithmic scores to the top
        .sort((alpha, beta) => beta.rank - alpha.rank)
        .map(wrapper => wrapper.data);
}

/**
 * Processes and prioritizes explore lookups for search.html (Bypasses Seen Blacklist)
 */
export function processSearchQuery(postsArray, queryText) {
    if (!Array.isArray(postsArray)) return [];
    const normalized = queryText.toLowerCase().trim();
    if (!normalized) return processMainFeed(postsArray);

    return postsArray
        .filter(post => {
            const contentString = `${post.content || ''} ${post.profiles?.username || ''}`.toLowerCase();
            return contentString.includes(normalized);
        })
        .map(post => ({
            data: post,
            rank: computePostScore(post)
        }))
        .sort((alpha, beta) => beta.rank - alpha.rank)
        .map(wrapper => wrapper.data);
}

/**
 * Captures an instantaneous interface interaction to boost content reach locally before database writes finish
 */
export function dispatchFeedBoost(postId, interactionType) {
    let logMsg = "";

    if (interactionType === 'like') {
        // Increment the fast memory cache instantly to maintain reach velocity even if database lags
        window.liveLikeCache[postId] = (window.liveLikeCache[postId] || 0) + 1;
        logMsg = `[ALGO CORES] Node ${postId} Liked. Instant velocity cache updated (+30% weight applied).`;
    } else if (interactionType === 'comment') {
        logMsg = `[ALGO CORES] Node ${postId} Commented. Supabase pipeline registering record (+60% weight applied).`;
    } else if (interactionType === 'view') {
        // Mark post as seen on timeline view retention rules
        window.sessionSeenList.add(postId);
        logMsg = `[ALGO CORES] Node ${postId} entered session seen list. Item hidden from future main feeds.`;
    }

    console.log(`%c${logMsg}`, "color: #1d9bf0; font-weight: bold; font-family: monospace;");
}

// Default export mapping for fallback dynamic module imports
export default dispatchFeedBoost;


// =========================================================================
// AUTOMATED INTERCEPTION LINKING LAYER
// Integrates rules directly into Supabase calls without touching homepage.js or search.js
// =========================================================================
(function selfHookSupabasePipeline() {
    // Locate the underlying prototype client constructor
    const coreFromMethod = window.supabase?.SupabaseClient?.prototype?.from || 
                           (window.vClient ? Object.getPrototypeOf(window.vClient).from : null);

    if (!coreFromMethod) {
        // Safe check: If Supabase initialization is running slow, retry in 100ms
        setTimeout(selfHookSupabasePipeline, 100);
        return;
    }

    const prototypeContext = window.supabase?.SupabaseClient?.prototype || Object.getPrototypeOf(window.vClient);

    if (prototypeContext && !prototypeContext._isAlgoLinked) {
        prototypeContext._isAlgoLinked = true;
        const nativeFrom = prototypeContext.from;

        prototypeContext.from = function(table) {
            const builder = nativeFrom.apply(this, arguments);

            if (table === 'posts') {
                const nativeSelect = builder.select;
                
                builder.select = function() {
                    const query = nativeSelect.apply(this, arguments);
                    const nativeThen = query.then;

                    // Intercept the data array when it returns from Supabase
                    query.then = function(onfulfilled, onrejected) {
                        return nativeThen.call(this, function(res) {
                            if (res && res.data && Array.isArray(res.data)) {
                                const currentUrl = window.location.pathname;
                                
                                if (currentUrl.includes('search.html')) {
                                    // Search Page Context: Run query matches, keep viewed posts discoverable
                                    const searchFieldVal = document.querySelector('.search-bar')?.value || '';
                                    res.data = processSearchQuery(res.data, searchFieldVal);
                                } else {
                                    // Main Home Feed Context: Run algorithm calculations and hide viewed nodes
                                    res.data = processMainFeed(res.data);
                                    
                                    // Append remaining items to session seen list once they load onto the feed card list
                                    res.data.forEach(item => {
                                        if (item && item.id) window.sessionSeenList.add(item.id);
                                    });
                                }
                            }
                            return onfulfilled(res);
                        }, onrejected);
                    };
                    return query;
                };
            }
            return builder;
        };
        console.log("%c[LINK COMPLETE] Algorithm linked to Supabase streams securely. Platform operational.", "color: #00ba7c; font-weight: bold;");
    }
})();
// =========================================================================
// AUTOMATED INTERCEPTION LINKING LAYER (Paste at the bottom of algorithm.js)
// Integrates rules directly into Supabase calls without touching homepage.js or search.js
// =========================================================================
(function selfHookSupabasePipeline() {
    // Locate the underlying prototype client constructor dynamically
    const coreFromMethod = window.supabase?.SupabaseClient?.prototype?.from || 
                           (window.vClient ? Object.getPrototypeOf(window.vClient).from : null);

    if (!coreFromMethod) {
        // Safe check: If Supabase initialization is running slow, retry in 100ms
        setTimeout(selfHookSupabasePipeline, 100);
        return;
    }

    const prototypeContext = window.supabase?.SupabaseClient?.prototype || Object.getPrototypeOf(window.vClient);

    if (prototypeContext && !prototypeContext._isAlgoLinked) {
        prototypeContext._isAlgoLinked = true;
        const nativeFrom = prototypeContext.from;

        prototypeContext.from = function(table) {
            const builder = nativeFrom.apply(this, arguments);

            if (table === 'posts') {
                const nativeSelect = builder.select;
                
                builder.select = function() {
                    const query = nativeSelect.apply(this, arguments);
                    const nativeThen = query.then;

                    // Intercept the data array when it returns from Supabase
                    query.then = function(onfulfilled, onrejected) {
                        return nativeThen.call(this, function(res) {
                            if (res && res.data && Array.isArray(res.data)) {
                                const currentUrl = window.location.pathname;
                                
                                if (currentUrl.includes('search.html')) {
                                    // Search Page Context: Run query matches, keep viewed posts discoverable
                                    const searchFieldVal = document.querySelector('.search-bar')?.value || '';
                                    res.data = processSearchQuery(res.data, searchFieldVal);
                                } else {
                                    // Main Home Feed Context: Run algorithm calculations and hide viewed nodes
                                    res.data = processMainFeed(res.data);
                                    
                                    // Append remaining items to session seen list once they load onto the feed card list
                                    res.data.forEach(item => {
                                        if (item && item.id) window.sessionSeenList.add(item.id);
                                    });
                                }
                            }
                            return onfulfilled(res);
                        }, onrejected);
                    };
                    return query;
                };
            }
            return builder;
        };
        console.log("%c[LINK COMPLETE] Algorithm linked to Supabase streams securely.", "color: #00ba7c; font-weight: bold;");
    }
})();
