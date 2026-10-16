import type { Metadata } from "next";
import ClubStory from "@/components/story/ClubStory";
import { clubSlugs, dossier } from "@/lib/data";

export function generateStaticParams() {
  return clubSlugs().map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  return { title: `${dossier(slug).club.name}, read in full — dugout` };
}

export default async function ClubStoryPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <ClubStory slug={slug} />;
}
