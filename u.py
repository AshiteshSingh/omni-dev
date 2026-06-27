import requests

# --- CONFIGURATION ---
USERNAME = "AshiteshSingh"
TOKEN = "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"
# ---------------------

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def get_all_users(url):
    """Helper function to handle GitHub API pagination and fetch all users."""
    users = set()
    while url:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Error fetching data: {response.json().get('message')}")
            break

        data = response.json()
        for user in data:
            users.add(user["login"])

        # Check if there's a next page in the Link header
        if "next" in response.links:
            url = response.links["next"]["url"]
        else:
            url = None
    return users


def main():
    print("Fetching your followers and the people you follow...")

    # Fetch users you follow
    following_url = f"https://api.github.com/users/{USERNAME}/following?per_page=100"
    following = get_all_users(following_url)

    # Fetch users who follow you
    followers_url = f"https://api.github.com/users/{USERNAME}/followers?per_page=100"
    followers = get_all_users(followers_url)

    # Identify people who do not follow you back
    not_following_back = following - followers

    print(f"\nYou follow: {len(following)} users.")
    print(f"Followers: {len(followers)} users.")
    print(f"Users not following you back: {len(not_following_back)}\n")

    if not not_following_back:
        print("Everyone you follow follows you back! Nothing to do.")
        return

    # Unfollow process
    for user in not_following_back:
        unfollow_url = f"https://api.github.com/user/following/{user}"
        response = requests.delete(unfollow_url, headers=headers)

        if response.status_code == 204:
            print(f"Successfully unfollowed: {user}")
        else:
            print(
                f"Failed to unfollow {user}: Status code {response.status_code}"
            )


if __name__ == "__main__":
    main()