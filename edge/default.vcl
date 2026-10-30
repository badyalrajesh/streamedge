vcl 4.1;

backend origin {
    .host = "origin";
    .port = "80";
}

sub vcl_recv {
    # Health check for the load balancer
    if (req.url == "/healthz") {
        return (synth(200, "OK"));
    }
}

sub vcl_backend_response {
    # If origin goes down, keep serving cached copies for up to 1 hour
    set beresp.grace = 1h;
}

sub vcl_deliver {
    # Add headers so we can SEE what the cache did
    if (obj.hits > 0) {
        set resp.http.X-Cache = "HIT";
    } else {
        set resp.http.X-Cache = "MISS";
    }
    set resp.http.X-Cache-Hits = obj.hits;
}
