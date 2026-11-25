# bangladesh-bazar
🇧🇩 বাংলাদেশ অনলাইন বাজার হলো একটি আধুনিক অনলাইন শপিং প্ল্যাটফর্ম, যেখানে দেশের যেকোনো প্রান্ত থেকে সহজেই বিভিন্ন ধরনের জনপ্রিয় ও প্রয়োজনীয় পণ্য অর্ডার করা যাবে। 🛍️
import express from "express";

const app = express();

const API_URL = "https://www.merrono.com/api/v3";

app.get("/api/products", async (req, res) => {
  try {
    const response = await fetch(
      `${API_URL}/products?per_page=20`,
      {
        headers: {
          Accept: "application/json",
          "X-API-KEY": process.env.MERRONO_API_KEY,
          "X-SECRET-KEY": process.env.MERRONO_SECRET_KEY
        }
      }
    );

    if (!response.ok) {
      return res.status(response.status).json({
        error: "Merrono API থেকে পণ্য আনা যায়নি"
      });
    }

    const result = await response.json();

    // API-এর আসল JSON কাঠামো অনুযায়ী
    // পণ্যের তালিকা ও price field মিলিয়ে নিতে হবে।
    const products = result.data || [];

    const updatedProducts = products.map(product => {
      const originalPrice = Number(product.price);

      return {
        ...product,
        selling_price: Number(
          (originalPrice * 1.15).toFixed(2)
        )
      };
    });

    res.json(updatedProducts);

  } catch (error) {
    res.status(500).json({
      error: "সার্ভারে সমস্যা হয়েছে"
    });
  }
});

app.listen(3000, () => {
  console.log("Bangladesh Bazar server চালু হয়েছে");
});
